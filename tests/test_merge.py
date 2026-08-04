from squarepeg.merge import deep_merge


def test_plain_merge_no_claims_overlay_wins_on_scalar():
    base = {"a": 1, "b": 2}
    overlay = {"b": 3, "c": 4}
    assert deep_merge(base, overlay) == {"a": 1, "b": 3, "c": 4}


def test_claim_protects_scalar_from_overlay():
    base = {"spec": {"restartPolicy": "Never"}}
    overlay = {"spec": {"restartPolicy": "Always"}}
    claims = frozenset({"/spec/restartPolicy"})
    result = deep_merge(base, overlay, claims, path="")
    assert result["spec"]["restartPolicy"] == "Never"


def test_claim_does_not_block_sibling_keys():
    base = {"spec": {"restartPolicy": "Never"}}
    overlay = {"spec": {"restartPolicy": "Always", "nodeSelector": {"x": "y"}}}
    claims = frozenset({"/spec/restartPolicy"})
    result = deep_merge(base, overlay, claims, path="")
    assert result["spec"]["restartPolicy"] == "Never"
    assert result["spec"]["nodeSelector"] == {"x": "y"}


def test_env_var_cli_claim_beats_passthrough():
    base = {
        "spec": {
            "containers": [{"name": "main", "env": [{"name": "FOO", "value": "cli-value"}]}],
        }
    }
    overlay = {
        "spec": {
            "containers": [{"name": "main", "env": [{"name": "FOO", "value": "passthrough-value"}]}],
        }
    }
    claims = frozenset({"/spec/containers/[name=main]/env/[name=FOO]"})
    result = deep_merge(base, overlay, claims, path="")
    env = {e["name"]: e["value"] for e in result["spec"]["containers"][0]["env"]}
    assert env["FOO"] == "cli-value"


def test_passthrough_env_var_still_lands_when_not_claimed():
    base = {"spec": {"containers": [{"name": "main", "env": [{"name": "FOO", "value": "cli-value"}]}]}}
    overlay = {"spec": {"containers": [{"name": "main", "env": [{"name": "BAR", "value": "from-config"}]}]}}
    result = deep_merge(base, overlay, frozenset(), path="")
    env = {e["name"]: e["value"] for e in result["spec"]["containers"][0]["env"]}
    assert env == {"FOO": "cli-value", "BAR": "from-config"}


def test_named_list_merge_for_containers():
    base = {"spec": {"containers": [{"name": "main", "image": "alpine"}]}}
    overlay = {"spec": {"containers": [{"name": "main", "securityContext": {"runAsNonRoot": True}}]}}
    result = deep_merge(base, overlay, frozenset(), path="")
    container = result["spec"]["containers"][0]
    assert container["image"] == "alpine"
    assert container["securityContext"] == {"runAsNonRoot": True}


def test_non_named_list_is_replaced_wholesale():
    base = {"spec": {"tolerations": [{"key": "a", "operator": "Exists"}]}}
    overlay = {"spec": {"tolerations": [{"key": "b", "operator": "Exists"}]}}
    result = deep_merge(base, overlay, frozenset(), path="")
    assert result["spec"]["tolerations"] == [{"key": "b", "operator": "Exists"}]


def test_non_named_list_can_be_cleared():
    base = {"spec": {"tolerations": [{"key": "a", "operator": "Exists"}]}}
    overlay = {"spec": {"tolerations": []}}
    result = deep_merge(base, overlay, frozenset(), path="")
    assert result["spec"]["tolerations"] == []


def test_labels_dict_merges_rather_than_replacing():
    base = {"metadata": {"labels": {"app.kubernetes.io/managed-by": "squarepeg"}}}
    overlay = {"metadata": {"labels": {"team": "bioinf"}}}
    result = deep_merge(base, overlay, frozenset(), path="")
    assert result["metadata"]["labels"] == {"app.kubernetes.io/managed-by": "squarepeg", "team": "bioinf"}


def test_type_mismatch_overlay_wins():
    base = {"x": {"nested": 1}}
    overlay = {"x": "scalar-now"}
    result = deep_merge(base, overlay, frozenset(), path="")
    assert result["x"] == "scalar-now"


def test_new_key_appears_from_overlay():
    base = {"spec": {"containers": [{"name": "main"}]}}
    overlay = {"spec": {"nodeSelector": {"disk": "ssd"}}}
    result = deep_merge(base, overlay, frozenset(), path="")
    assert result["spec"]["nodeSelector"] == {"disk": "ssd"}
    assert result["spec"]["containers"] == [{"name": "main"}]
