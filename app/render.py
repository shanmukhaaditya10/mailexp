import re


def join_names(names):
    names = [n for n in names if n]
    if not names:
        return "there"
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return "team"


def reachable(founders):
    """Founders we will actually email: have an address and are not flagged invalid."""
    return [f for f in founders if f.get("email") and not f.get("invalid")]


def variables(lead, founders):
    firsts = [f["first_name"] for f in reachable(founders) if f.get("first_name")]
    joined = join_names(firsts)
    return {
        "names": joined,
        "first_name": joined,
        "company": lead.get("company") or "your company",
        "one_liner": lead.get("context") or "",
    }


def render(template, vars_):
    def repl(m):
        key = m.group(1).strip()
        return str(vars_.get(key, m.group(0)))

    return re.sub(r"\{\{\s*([a-zA-Z_]+)\s*\}\}", repl, template or "")
