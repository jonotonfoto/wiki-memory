# atomic.py
"""Atomic file write context manager — two-phase commit for wiki pages.

Writes to a ``.tmp`` file in the SAME directory as the target (required for
``os.replace`` atomicity on NTFS — cross-volume rename raises ``EXDEV``),
then ``flush()`` + ``fsync()`` + ``os.replace()``.

Two-phase commit flow (caller orchestrates DB + file together)::

    db.upsert_page(..., content_hash='PENDING')        # phase 1: mark
    with atomic_write(path):                           # file written
        f.write(content)
    # ≡ inside __exit__: flush → fsync → close → os.replace
    db.update_page_hash(slug, real_hash)                # phase 2: finalize

If the process dies before ``os.replace``, the ``.tmp`` file is left on disk
but the target does not exist.  The caller writes ``content_hash='PENDING'`` to
the DB BEFORE the replace, so a crash leaves a PENDING row + orphan tmp — both
cleaned up at next startup by ``indexer.cleanup_pending()``.

Important: if ``os.replace`` itself raises (or the process is hard-killed at
that exact point), the flushed+fsynced ``.tmp`` file is LEFT in place rather
than deleted — it contains valid data and is cleaned up later by the pending
orphan sweep.  Exceptions earlier (during ``write``/``flush``/``fsync``) DO
remove the tmp, because its contents may be incomplete/corrupt.
"""
import contextlib
import os


@contextlib.contextmanager
def atomic_write(path: str, mode: str = "w", encoding: str = "utf-8",
                 newline: str = "\n", on_commit=None):
    """Context manager: write to ``{path}.tmp`` then ``os.replace`` to ``path``.

    The temp file is always in the same directory as ``path`` (same volume),
    which is required for ``os.replace`` atomicity on Windows/NTFS.

    ``newline="\\n"`` keeps byte-identical output on Windows (no ``\\r\\n``
    translation), so content hashes from the in-memory string match hashes
    from the file on disk.

    Concurrency: the fixed ``{path}.tmp`` name is safe — the wiki indexer
    holds an exclusive file lock (``index_lock.py``): exactly ONE writer.

    Yields a file object.  On successful ``__exit__``:
      1. ``f.flush()`` + ``os.fsync()`` (flush OS cache to disk)
      2. ``f.close()``
      3. ``os.replace(tmp, path)`` — atomic rename on same volume
      4. ``on_commit()`` called if provided (e.g. DB hash update)

    Exception handling:
      - Exception during write/flush/fsync  → remove tmp (it may be incomplete),
        propagate exception.
      - Exception during ``os.replace``     → leave the flushed+fsynced tmp on
        disk (it holds valid data, will be reclaimed by cleanup_pending()),
        propagate exception.
      - Exception during ``on_commit``       → propagate; tmp already renamed
        to target, cleanup_pending() will still reclaim it via the DB row.
    """
    tmp = path + ".tmp"
    # open with the encoding the caller expects (newline fixed to \n so the
    # on-disk bytes match the in-memory string → hashes agree on Windows)
    f = open(tmp, mode, encoding=encoding, newline=newline) if "b" not in mode else open(tmp, mode)
    _phase = "write"  # write -> rename -> commit
    try:
        yield f
        f.flush()
        os.fsync(f.fileno())
        f.close()
        f = None  # ownership transferred; don't double-close
        # Atomic rename (same volume guaranteed since tmp is in same dir)
        _phase = "rename"
        os.replace(tmp, path)
        # Phase 2 callback (e.g. update DB hash AFTER successful rename)
        _phase = "commit"
        if on_commit is not None:
            on_commit()
    except BaseException:
        if f is not None:
            try:
                f.close()
            except Exception:
                pass
        # If we failed during write/flush/fsync (before os.replace), the tmp
        # file may be incomplete → remove it.  Once os.replace has been reached
        # we leave the tmp (it holds valid, fsynced data) for cleanup_pending()
        # to reclaim at next startup — mimicking a hard-reboot boundary.
        if _phase == "write" and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
        raise
