from conftest import register_text


def test_original_head_range_etag_and_conditional_authorization(env):
    receipt = register_text(env, value="conditional original bytes")
    path = f"/v1/sources/{receipt['source_id']}/versions/{receipt['source_version_id']}/original"
    get = env["http"].get(path, headers=env["headers"])
    assert get.status_code == 200
    head = env["http"].head(path, headers=env["headers"])
    assert head.status_code == 200 and not head.content
    assert head.headers["content-length"] == str(len(get.content))
    assert head.headers["etag"] == get.headers["etag"]
    assert (
        env["http"]
        .get(path, headers=env["headers"] | {"If-None-Match": head.headers["etag"]})
        .status_code
        == 304
    )
    partial = env["http"].get(
        path, headers=env["headers"] | {"Range": "bytes=1-4", "If-Range": head.headers["etag"]}
    )
    assert partial.status_code == 206 and partial.content == get.content[1:5]
    fallback = env["http"].get(
        path, headers=env["headers"] | {"Range": "bytes=1-4", "If-Range": '"stale"'}
    )
    assert fallback.status_code == 200 and fallback.content == get.content
    env["s"].revoke(env["a"], "revoke", receipt["source_id"])
    assert env["http"].head(path, headers=env["headers"]).status_code == 404
    assert (
        env["http"]
        .get(path, headers=env["headers"] | {"If-None-Match": head.headers["etag"]})
        .status_code
        == 404
    )
