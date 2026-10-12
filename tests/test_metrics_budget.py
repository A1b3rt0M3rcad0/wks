from wks_api.http.app import REQUESTS


def test_arbitrary_http_methods_use_a_bounded_metric_label(env):
    response = env["http"].request("UNTRUSTED-METHOD", "/health/live")
    assert response.status_code in {404, 405}
    labels = [
        sample.labels["method"]
        for metric in REQUESTS.collect()
        for sample in metric.samples
        if "method" in sample.labels
    ]
    assert "OTHER" in labels and "UNTRUSTED-METHOD" not in labels
