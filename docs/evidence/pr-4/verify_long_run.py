"""Verify captured synthetic QQ output and the redacted Gateway interval."""

import json
from pathlib import Path


def main():
    root = Path(__file__).parent / "2026-08-29-long-run"
    capture = json.loads((root / "qq-capture.json").read_text())
    bodies = capture["carriers"]
    assert [len(body) for body in bodies] == [78, 4000, 4000, 2318]
    progress = "QQR3_START_0036" + "".join(
        f"QQR3_STEP_{step}_OF_7_0036" for step in range(1, 8)
    )
    final = " ".join([
        "QQ_P1_RETEST3_FINAL_BEGIN_0036",
        *(f"L{line:03d}:" + "X" * 100 for line in range(1, 97)),
        "QQ_P1_RETEST3_FINAL_OK_0036",
    ])
    assert len(final) == 10234
    actual = "".join(bodies)
    assert actual == progress + final
    assert actual.count("QQ_P1_RETEST3_FINAL_BEGIN_0036") == 1
    assert actual.count("QQ_P1_RETEST3_FINAL_OK_0036") == 1

    log = (root / "gateway-interval.txt").read_text()
    assert log.count("QQ C2C stream opened") == 4
    assert log.count("silent age rollover sealed") == 1
    assert log.count("overflow chunk sealed") == 2
    assert log.count("QQ C2C stream sealed") == 1
    assert "age=480.5s" in log
    assert "WebSocket closed: code=4009" in log
    assert "WebSocket connected" in log and "Session resumed" in log
    assert "Suppressing normal final send" in log
    assert "time=872.5s api_calls=1 response=10234 chars" in log
    for error in ("40034128", "ERROR", "index需要递增", "index needs to",
                  "lifetime response", "attempt 2", "fallback", "failed"):
        assert error not in log, error
    print("qq_long_run_captured_output_and_lifecycle=ok")


def verify_review_retest():
    root = Path(__file__).parent / "2026-08-31-review-retest"
    suffix = json.loads((root / "qq-suffix-capture.json").read_text())
    assert suffix["response_carriers"] == ["status NOTFINALFINAL"]
    short_log = (root / "gateway-suffix.txt").read_text()
    assert short_log.count("QQ C2C stream opened") == 1
    assert short_log.count("QQ C2C stream sealed") == 1
    assert "Suppressing normal final send" in short_log

    capture = json.loads((root / "qq-overflow-capture.json").read_text())
    bodies = capture["carriers"]
    assert [len(body) for body in bodies] == [4000, 4000, 2305]
    final = " ".join([
        "R20_BEGIN",
        *(f"L{line:03d}: " + "X" * 100 for line in range(1, 97)),
        "R20_OK",
    ])
    assert len(final) == 10288
    actual = "".join(bodies)
    assert actual == "R20_STARTR20_STEP" + final
    assert actual.count("R20_BEGIN") == actual.count("R20_OK") == 1
    log = (root / "gateway-overflow.txt").read_text()
    assert log.count("QQ C2C stream opened") == 3
    assert log.count("overflow chunk sealed") == 2
    assert log.count("QQ C2C stream sealed") == 1
    assert "Suppressing normal final send" in log
    assert "time=62.6s api_calls=1 response=10288 chars" in log
    print("qq_review_retest_captured_output_and_lifecycle=ok")


if __name__ == "__main__":
    main()
    verify_review_retest()
