from wks_api.http.app import create_app


def test_every_public_http_success_has_an_explicit_schema():
    spec = create_app().openapi()
    for path, verbs in spec["paths"].items():
        for verb, operation in verbs.items():
            for status, response in operation.get("responses", {}).items():
                if status.startswith("2") and verb != "head":
                    assert any(
                        value.get("schema") for value in response.get("content", {}).values()
                    ), (path, verb, status)
