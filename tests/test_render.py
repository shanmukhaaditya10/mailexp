from app import render


def test_join_names_none():
    assert render.join_names([]) == "there"
    assert render.join_names([None, ""]) == "there"


def test_join_names_one():
    assert render.join_names(["Jane"]) == "Jane"


def test_join_names_two():
    assert render.join_names(["Jane", "Bob"]) == "Jane and Bob"


def test_join_names_three_or_more_caps_to_team():
    assert render.join_names(["Raj", "Rushil", "Samay"]) == "team"
    assert render.join_names(["Jason", "Naijide", "Garreth", "Kenneth"]) == "team"


def test_variables_first_name_matches_names_join():
    lead = {"company": "Acme", "context": ""}
    founders = [{"first_name": "Raj", "email": "raj@acme.com"},
                {"first_name": "Rushil", "email": "rushil@acme.com"},
                {"first_name": "Samay", "email": "samay@acme.com"},
                {"first_name": "Shloke", "email": "shloke@acme.com"}]
    vars_ = render.variables(lead, founders)
    assert vars_["names"] == "team"
    assert vars_["first_name"] == "team"


def test_variables_two_founders_join():
    lead = {"company": "Acme", "context": ""}
    founders = [{"first_name": "Serena", "email": "serena@acme.com"},
                {"first_name": "Charley", "email": "charley@acme.com"}]
    vars_ = render.variables(lead, founders)
    assert vars_["first_name"] == "Serena and Charley"
    assert vars_["names"] == "Serena and Charley"


def test_variables_excludes_invalid_and_emailless_founders():
    lead = {"company": "Acme", "context": ""}
    founders = [
        {"first_name": "Oleg", "email": "oleg@acme.com", "invalid": 1},
        {"first_name": "Yuriy", "email": "yuriy@acme.com", "invalid": 1},
        {"first_name": "Nina", "email": "nina@acme.com", "invalid": 0},
        {"first_name": "Ghost", "email": "", "invalid": 0},
    ]
    vars_ = render.variables(lead, founders)
    assert vars_["first_name"] == "Nina"
    assert vars_["names"] == "Nina"
