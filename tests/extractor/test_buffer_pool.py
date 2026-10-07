"""Unit tests for the bounded buffer pool (Task 3, plan § 5).

The pool is the Extractor's memory ceiling: at most ``capacity`` bytearrays —
the configured extraction concurrency — ever exist. Borrowed buffers come back
with their length reset so no request can see another request's bytes, and the
pool hands out ``None`` instead of growing past its top. ``memoryview`` slices
of a pooled buffer alias its memory, which is what lets consumers read the used
region without a copy.
"""

from pdfextractor.infrastructure.memory.pool import BufferPool


def test_borrowed_buffers_are_reused_by_identity_within_the_top() -> None:
    pool = BufferPool(capacity=2)
    first = pool.acquire()
    second = pool.acquire()
    assert first is not None
    assert second is not None
    assert first is not second

    pool.release(first)
    pool.release(second)

    again_one = pool.acquire()
    again_two = pool.acquire()
    assert again_one is not None
    assert again_two is not None
    assert {id(again_one), id(again_two)} == {id(first), id(second)}


def test_a_dirty_buffer_borrowed_again_starts_empty() -> None:
    pool = BufferPool(capacity=1)
    buffer = pool.acquire()
    assert buffer is not None
    buffer.extend(b"documento confidencial del request anterior")

    pool.release(buffer)
    borrowed = pool.acquire()

    assert borrowed is buffer
    assert len(borrowed) == 0
    borrowed.extend(b"contenido nuevo")
    assert bytes(borrowed) == b"contenido nuevo"


def test_pool_returns_none_once_every_buffer_is_out() -> None:
    pool = BufferPool(capacity=2)
    first = pool.acquire()
    second = pool.acquire()
    assert first is not None
    assert second is not None

    assert pool.acquire() is None

    pool.release(first)
    reused = pool.acquire()
    assert reused is first
    assert pool.acquire() is None

    pool.release(reused)
    pool.release(second)
    assert pool.acquire() is not None


def test_new_buffers_are_only_born_below_the_top() -> None:
    pool = BufferPool(capacity=1)
    only = pool.acquire()
    assert only is not None
    assert pool.acquire() is None

    pool.release(only)
    assert pool.acquire() is only


def test_releasing_extra_buffers_never_grows_the_pool_past_its_capacity() -> None:
    pool = BufferPool(capacity=1)
    buffer = pool.acquire()
    assert buffer is not None
    pool.release(buffer)

    pool.release(bytearray(b"foreign buffer"))
    pool.release(bytearray(b"another foreign buffer"))

    assert pool.acquire() is buffer
    assert pool.acquire() is None


def test_memoryview_slices_of_a_pooled_buffer_alias_its_memory() -> None:
    pool = BufferPool(capacity=1)
    buffer = pool.acquire()
    assert buffer is not None
    buffer.extend(b"0123456789")

    view = memoryview(buffer)[:4]
    assert bytes(view) == b"0123"

    buffer[0:4] = b"9876"
    assert bytes(view) == b"9876"
