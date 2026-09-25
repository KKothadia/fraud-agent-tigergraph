"""
Isolated test: verifies that:
1. resilient_retry correctly cycles through 5 attempts with exponential backoff.
2. Hard timeout fires and does NOT block the retry loop (shutdown wait=False).
3. Permanent errors are NOT retried.
4. Checkpoint save/load/atomic write works.
"""
import os
import sys
import json
import time
import concurrent.futures
import threading
import tempfile

CALL_TIMEOUT_S = 2  # short for testing

def resilient_retry(operation_name, func, *args, **kwargs):
    max_retries = 5
    backoff_times = [0.05, 0.1, 0.15, 0.2, 0.3]  # shortened for testing

    last_exc = None
    for attempt in range(max_retries):
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = executor.submit(func, *args, **kwargs)
        try:
            result = future.result(timeout=CALL_TIMEOUT_S)
            executor.shutdown(wait=False)
            return result
        except concurrent.futures.TimeoutError:
            executor.shutdown(wait=False)   # abandon hung thread, do NOT block
            exc_name = "CallTimeout"
            exc_msg = f"Call did not complete within {CALL_TIMEOUT_S}s"
            last_exc = TimeoutError(exc_msg)
        except Exception as e:
            executor.shutdown(wait=False)
            exc_name = type(e).__name__
            exc_msg = str(e)
            last_exc = e
            err_lower = exc_msg.lower()
            if any(tok in err_lower for tok in ("rest-30200", "type conversion",
                                                 "invalid json", "no such vertex type",
                                                 "no such edge type")):
                print(f"[FATAL] Permanent error in {operation_name}: {exc_name}: {exc_msg}")
                raise e

        if attempt == max_retries - 1:
            print(f"[FATAL] All {max_retries} retries exhausted for {operation_name}. Last error: {exc_name}")
            raise last_exc

        sleep_time = backoff_times[attempt]
        print(f"[RETRY] TigerGraph request failed ({operation_name}); retry {attempt+1}/{max_retries} in {sleep_time}s: {exc_name}")
        time.sleep(sleep_time)

# ---- Tests ----
PASS = 0
FAIL = 0

def test(name, cond):
    global PASS, FAIL
    if cond:
        print(f"  PASS: {name}")
        PASS += 1
    else:
        print(f"  FAIL: {name}")
        FAIL += 1

print("\n=== Test 1: Successful call returns value ===")
result = resilient_retry("test_ok", lambda: 42)
test("returns value", result == 42)

print("\n=== Test 2: All 5 retries fire on transient error ===")
attempts = []
def flaky():
    attempts.append(1)
    raise ConnectionResetError("transient")
try:
    resilient_retry("test_transient", flaky)
except Exception:
    pass
test("5 attempts made", len(attempts) == 5)

print("\n=== Test 3: Hard timeout fires and does NOT block ===")
# Use a threading.Event so the background thread can self-terminate
_stop = threading.Event()
def hang_with_event():
    _stop.wait(timeout=60)  # will exit quickly once event is set

t0 = time.time()
try:
    resilient_retry("test_hang", hang_with_event)
except Exception:
    pass
finally:
    _stop.set()  # release all waiting threads
elapsed = time.time() - t0
# 5 retries × 2s timeout + 4 backoffs (0.05+0.1+0.15+0.2) = ~10.5s
test("timeout fires and loop does NOT block", elapsed < 15)
print(f"  (elapsed: {elapsed:.1f}s)")

print("\n=== Test 4: Permanent error NOT retried ===")
perm_attempts = []
def perm_error():
    perm_attempts.append(1)
    raise Exception("REST-30200 type conversion error")
try:
    resilient_retry("test_perm", perm_error)
except Exception:
    pass
test("only 1 attempt on permanent error", len(perm_attempts) == 1)

print("\n=== Test 5: Checkpoint atomic write ===")
with tempfile.TemporaryDirectory() as tmpdir:
    ckpt_file = os.path.join(tmpdir, "load_checkpoint.json")

    def save_checkpoint(state):
        tmp = ckpt_file + ".tmp"
        with open(tmp, 'w') as f:
            json.dump(state, f, indent=2)
        os.replace(tmp, ckpt_file)

    def load_checkpoint():
        if os.path.exists(ckpt_file):
            with open(ckpt_file, 'r') as f:
                return json.load(f)
        return {"transaction_chunks_completed": []}

    state = load_checkpoint()
    state["transaction_chunks_completed"].append(1)
    save_checkpoint(state)
    state2 = load_checkpoint()
    test("chunk recorded", 1 in state2["transaction_chunks_completed"])
    test("no .tmp file left", not os.path.exists(ckpt_file + ".tmp"))

print(f"\n=== Results: {PASS} passed, {FAIL} failed ===")
sys.exit(0 if FAIL == 0 else 1)
