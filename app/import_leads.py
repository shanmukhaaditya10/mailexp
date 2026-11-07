import re
from pathlib import Path

from openpyxl import load_workbook

from .db import DEFAULT_LIST_STEM, ROOT, get_conn, init_db
from .util import is_valid_email

XLS_DIR = ROOT / "xls"
XLSX = XLS_DIR / "yc_founders_outreach.xlsx"

# Columns that must exist in the workbook header.
REQUIRED_COLUMNS = ["Company"]

MAX_REPORTED_ERRORS = 100


def _norm(value):
    """Collapse whitespace and trim; None -> ''."""
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def _key(value):
    return _norm(value).casefold()


def _present(value):
    """True when a workbook value carries information worth writing."""
    if value is None:
        return False
    return str(value).strip() != ""


# Directories that are never scanned for lead workbooks.
SKIP_DIRS = {"data", "web", "tests", "app", "__pycache__"}


def _real_xlsx(path):
    return path.is_file() and not path.name.startswith("~$")


def list_files():
    """Every real xlsx in the ``xls`` folder or one level of its subfolders.

    Root files keep their plain stem as the list id (e.g. ``yc_founders_outreach``);
    subfolder files use a relative path id (e.g. ``yc_25-27_1-50/batch-1``).
    """
    if not XLS_DIR.is_dir():
        return []
    out = [p for p in sorted(XLS_DIR.glob("*.xlsx")) if _real_xlsx(p)]
    for sub in sorted(XLS_DIR.iterdir()):
        if not sub.is_dir() or sub.name.startswith(".") or sub.name in SKIP_DIRS:
            continue
        out.extend(p for p in sorted(sub.glob("*.xlsx")) if _real_xlsx(p))
    return out


def list_id(path):
    """Stable list id for a workbook: its path relative to the xls folder, sans extension."""
    try:
        rel = Path(path).resolve().relative_to(XLS_DIR.resolve())
    except (OSError, ValueError):
        return Path(path).stem
    return rel.with_suffix("").as_posix()


def resolve_list(stem):
    """Map a list id to its xlsx path.

    A list id is either a plain stem (file in the project root) or a
    ``folder/name`` relative path (file one level down). The `list` query
    parameter can never resolve to an arbitrary local file.
    """
    if not stem or "\\" in stem or stem.startswith(".") or ".." in stem:
        return None
    parts = stem.split("/")
    if len(parts) > 2 or any(not p or p.startswith(".") or p in SKIP_DIRS for p in parts):
        return None
    candidate = (XLS_DIR / f"{stem}.xlsx")
    try:
        resolved = candidate.resolve()
        if resolved.parent != XLS_DIR.resolve() and resolved.parent.parent != XLS_DIR.resolve():
            return None
    except OSError:
        return None
    return resolved if resolved.is_file() else None


def import_from_xlsx(path=None, source=None):
    path = Path(path) if path else XLSX
    if not path.exists():
        raise FileNotFoundError(f"xlsx not found: {path}")
    source = source or path.stem or DEFAULT_LIST_STEM

    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb["Companies"] if "Companies" in wb.sheetnames else wb.active
        rows = list(ws.iter_rows(values_only=True))
    finally:
        wb.close()

    if not rows:
        raise ValueError("workbook is empty")

    header = [_norm(h) for h in rows[0]]
    idx = {name: i for i, name in enumerate(header)}
    missing = [c for c in REQUIRED_COLUMNS if c not in idx]
    if missing:
        raise ValueError("missing required column(s): " + ", ".join(missing))

    def col(row, name):
        i = idx.get(name)
        return row[i] if i is not None and i < len(row) else None

    conn = get_conn()
    try:
        lead_by_company = {
            _key(r["company"]): r["id"]
            for r in conn.execute("SELECT id, company FROM leads").fetchall()
        }
        founder_by_key = {}
        for r in conn.execute("SELECT id, lead_id, name, email FROM founders").fetchall():
            founder_by_key[(r["lead_id"], _key(r["name"]))] = dict(r)

        created_leads = updated_leads = 0
        created_founders = updated_founders = 0
        duplicates = 0
        seen_rows = set()
        errors = []

        for rowno, row in enumerate(rows[1:], start=2):
            company = _norm(col(row, "Company"))
            if not company:
                if any(_present(v) for v in row):
                    errors.append({"row": rowno, "error": "missing Company"})
                continue

            founder_name = _norm(col(row, "Founder Name"))
            email = _norm(col(row, "Email (guessed)"))

            row_key = (company.casefold(), founder_name.casefold())
            if row_key in seen_rows:
                duplicates += 1
                continue
            seen_rows.add(row_key)

            domain = _norm(col(row, "Domain"))
            if not domain and email and "@" in email:
                domain = email.split("@")[-1]

            # ---- upsert the lead (never touch user-edited context/status) ----
            lead_id = lead_by_company.get(company.casefold())
            lead_fields = {
                "batch": _norm(col(row, "Batch")),
                "location": _norm(col(row, "Location")),
                "domain": domain,
                "website": _norm(col(row, "Company Website")),
            }
            team_size = col(row, "Team Size")
            if lead_id is None:
                cur = conn.execute(
                    """INSERT INTO leads
                       (company, batch, team_size, location, domain, website, source, context, status, step)
                       VALUES (?,?,?,?,?,?,?, '', 'not_contacted', 0)""",
                    (company, lead_fields["batch"] or None,
                     team_size if _present(team_size) else None,
                     lead_fields["location"] or None, lead_fields["domain"] or None,
                     lead_fields["website"] or None, source),
                )
                lead_id = cur.lastrowid
                lead_by_company[company.casefold()] = lead_id
                created_leads += 1
            else:
                updates = {k: v for k, v in lead_fields.items() if v}
                if _present(team_size):
                    updates["team_size"] = team_size
                # keep an existing list association unless this is its home list
                updates["source"] = source
                if updates:
                    sets = ", ".join(f"{k}=?" for k in updates)
                    conn.execute(
                        f"UPDATE leads SET {sets} WHERE id=?",
                        (*updates.values(), lead_id),
                    )
                    updated_leads += 1

            # ---- upsert the founder ----
            if not founder_name:
                continue
            if email and not is_valid_email(email):
                errors.append({"row": rowno, "error": f"invalid email '{email}' for {founder_name}"})
                email = ""

            fkey = (lead_id, founder_name.casefold())
            first = founder_name.split()[0]
            fvals = {
                "title": _norm(col(row, "Founder Title")),
                "email": email,
                "linkedin": _norm(col(row, "Founder LinkedIn")),
                "twitter": _norm(col(row, "Founder Twitter")),
            }
            # The workbook may carry an "Invalid" flag (a bounced / bad address).
            # Only touch the column when the sheet actually has it, so lists
            # without the column never clear an existing flag.
            has_invalid = "Invalid" in idx
            if has_invalid:
                fvals["invalid"] = 1 if _norm(col(row, "Invalid")).lower() in (
                    "yes", "y", "true", "1") else 0
            if fkey not in founder_by_key:
                conn.execute(
                    """INSERT INTO founders (lead_id, name, first_name, title, email, linkedin, twitter, invalid)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (lead_id, founder_name, first, fvals["title"] or None,
                     fvals["email"] or None, fvals["linkedin"] or None, fvals["twitter"] or None,
                     fvals.get("invalid", 0)),
                )
                founder_by_key[fkey] = {"id": None, "lead_id": lead_id, "name": founder_name,
                                        "email": fvals["email"]}
                created_founders += 1
            else:
                updates = {k: v for k, v in fvals.items() if v or k == "invalid"}
                if updates:
                    sets = ", ".join(f"{k}=?" for k in updates)
                    conn.execute(
                        f"UPDATE founders SET {sets} WHERE lead_id=? AND name=?",
                        (*updates.values(), lead_id, founder_by_key[fkey]["name"]),
                    )
                    updated_founders += 1

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {
        "source": source,
        "file": path.name,
        # `leads`/`founders` retained for the existing UI flash message.
        "leads": created_leads,
        "founders": created_founders,
        "leads_created": created_leads,
        "leads_updated": updated_leads,
        "founders_created": created_founders,
        "founders_updated": updated_founders,
        "duplicates": duplicates,
        "errors": errors[:MAX_REPORTED_ERRORS],
        "error_count": len(errors),
    }


if __name__ == "__main__":
    init_db()
    print(import_from_xlsx())
