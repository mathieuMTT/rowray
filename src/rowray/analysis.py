PRICE_PER_TIB = 6.25
NUMERIC_TYPES = {"INTEGER", "INT64", "FLOAT", "FLOAT64", "NUMERIC", "BIGNUMERIC", "DECIMAL", "BIGDECIMAL"}
SKIPPED_TYPES = {"RECORD", "STRUCT", "GEOGRAPHY", "JSON"}


def schema_changes(
    base: list[tuple[str, str]], head: list[tuple[str, str]]
) -> tuple[list[str], list[str], list[str]]:
    base_types = dict(base)
    head_types = dict(head)
    added = [name for name in head_types if name not in base_types]
    removed = [name for name in base_types if name not in head_types]
    changed = [
        f"{name} {base_types[name]} → {head_types[name]}"
        for name in head_types
        if name in base_types and head_types[name] != base_types[name]
    ]
    return added, removed, changed


def profile_columns(
    base: list[tuple[str, str, str]], head: list[tuple[str, str, str]]
) -> tuple[list[tuple[str, str]], list[str]]:
    head_by_name = {name: (field_type, mode) for name, field_type, mode in head}
    shared = [
        (name, field_type, head_by_name[name][1])
        for name, field_type, _mode in base
        if name in head_by_name and head_by_name[name][0] == field_type
    ]
    kept = [(name, field_type) for name, field_type, mode in shared if mode != "REPEATED" and field_type not in SKIPPED_TYPES]
    skipped = [name for name, field_type, mode in shared if mode == "REPEATED" or field_type in SKIPPED_TYPES]
    return kept, skipped


def identifier(name: str) -> str:
    return f"`{name.replace('`', '')}`"


def profile_sql(base_sql: str, head_sql: str, columns: list[tuple[str, str]]) -> str:
    metrics = ["COUNT(*) AS n_rows"]
    for name, field_type in columns:
        column = identifier(name)
        metrics.append(f"COUNTIF({column} IS NULL) AS {identifier(name + '__nulls')}")
        metrics.append(f"APPROX_COUNT_DISTINCT({column}) AS {identifier(name + '__distincts')}")
        if field_type in NUMERIC_TYPES:
            metrics.append(f"SUM({column}) AS {identifier(name + '__sum')}")
    body = ",\n    ".join(metrics)
    return (
        "WITH base AS (\n"
        f"{base_sql}\n"
        "), head AS (\n"
        f"{head_sql}\n"
        ")\n"
        f"SELECT 'base' AS side, {body} FROM base\n"
        "UNION ALL\n"
        f"SELECT 'head' AS side, {body} FROM head"
    )


def focus_sql(base_sql: str, head_sql: str, column: str) -> str:
    name = identifier(column)
    return (
        "WITH base AS (\n"
        f"{base_sql}\n"
        "), head AS (\n"
        f"{head_sql}\n"
        "), counts AS (\n"
        f"    SELECT {name} AS value, COUNT(*) AS base_rows, 0 AS head_rows FROM base GROUP BY value\n"
        "    UNION ALL\n"
        f"    SELECT {name} AS value, 0 AS base_rows, COUNT(*) AS head_rows FROM head GROUP BY value\n"
        ")\n"
        "SELECT value, SUM(base_rows) AS base_rows, SUM(head_rows) AS head_rows\n"
        "FROM counts GROUP BY value\n"
        "ORDER BY GREATEST(base_rows, head_rows) DESC"
    )


def percentage(part: float, total: float) -> str:
    return "0%" if not total else f"{100 * part / total:.1f}%"


def delta(old: float, new: float) -> str:
    if old == 0:
        return "0" if new == 0 else "new"
    return f"{(new - old) / old:+.1%}"


def column_moved(base: dict, head: dict, name: str, field_type: str) -> bool:
    if percentage(base[name + "__nulls"], base["n_rows"]) != percentage(head[name + "__nulls"], head["n_rows"]):
        return True
    if int(base[name + "__distincts"]) != int(head[name + "__distincts"]):
        return True
    return field_type in NUMERIC_TYPES and base[name + "__sum"] != head[name + "__sum"]


def terminal_table(headers: tuple[str, ...], rows: list[tuple[str, ...]], right: set[int] | None = None) -> list[str]:
    right = right or set()
    widths = [max(len(headers[index]), *(len(row[index]) for row in rows)) for index in range(len(headers))]

    def border(left: str, middle: str, end: str) -> str:
        return left + middle.join("─" * (width + 2) for width in widths) + end

    def line(row: tuple[str, ...]) -> str:
        cells = [value.rjust(widths[index]) if index in right else value.ljust(widths[index]) for index, value in enumerate(row)]
        return "│ " + " │ ".join(cells) + " │"

    return [
        border("┌", "┬", "┐"),
        line(headers),
        border("├", "┼", "┤"),
        *(line(row) for row in rows),
        border("└", "┴", "┘"),
    ]


def focus_report(column: str, rows: list[dict] | None, note: str | None = None) -> str:
    lines = [f"Column focus: {column}"]
    if rows is None:
        lines.append(note or "Focus not run.")
        return "\n".join(lines)
    table_rows = []
    for row in rows:
        value = "NULL" if row["value"] is None else "".join(char if char.isprintable() else " " for char in str(row["value"]))
        base_rows = int(row["base_rows"])
        head_rows = int(row["head_rows"])
        table_rows.append((value.replace("│", "|"), f"{base_rows:,}", f"{head_rows:,}", delta(base_rows, head_rows)))
    return "\n".join(lines + terminal_table(("Value", "BASE rows", "WORKTREE rows", "Variation"), table_rows, {1, 2, 3}))


def format_bytes(value: int) -> str:
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024:
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.2f} TiB"


def format_delta(base: int, head: int) -> str:
    if not base:
        return "0.0%" if not head else "n/a"
    return f"{(head - base) / base:+.1%}"


def report(
    base: int,
    head: int,
    base_columns: list[tuple[str, str, str]],
    head_columns: list[tuple[str, str, str]],
    columns: list[tuple[str, str]],
    skipped: list[str],
    profile: dict[str, dict] | None,
    profile_note: str,
    full_refresh: tuple[int, int] | None = None,
) -> str:
    base_cost = base / 1024**4 * PRICE_PER_TIB
    head_cost = head / 1024**4 * PRICE_PER_TIB
    width = 20 if full_refresh else 12
    lines = [f"{'':{width}}{'BASE':>14}{'WORKTREE':>14}{'DELTA':>14}"]
    if full_refresh:
        full_base, full_head = full_refresh
        full_base_cost = full_base / 1024**4 * PRICE_PER_TIB
        full_head_cost = full_head / 1024**4 * PRICE_PER_TIB
        lines.extend(
            [
                f"{'Incremental scan':{width}}{format_bytes(base):>14}{format_bytes(head):>14}{format_delta(base, head):>14}",
                f"{'Incremental cost':{width}}{f'${base_cost:.4f}':>14}{f'${head_cost:.4f}':>14}{f'${head_cost - base_cost:+.4f}':>14}",
                f"{'Full-refresh scan':{width}}{format_bytes(full_base):>14}{format_bytes(full_head):>14}{format_delta(full_base, full_head):>14}",
                f"{'Full-refresh cost':{width}}{f'${full_base_cost:.4f}':>14}{f'${full_head_cost:.4f}':>14}{f'${full_head_cost - full_base_cost:+.4f}':>14}",
            ]
        )
    else:
        lines.extend(
            [
                f"{'Scan':{width}}{format_bytes(base):>14}{format_bytes(head):>14}{format_delta(base, head):>14}",
                f"{'Cost (USD)':{width}}{f'${base_cost:.4f}':>14}{f'${head_cost:.4f}':>14}{f'${head_cost - base_cost:+.4f}':>14}",
            ]
        )
    lines.append("")
    added, removed, changed = schema_changes(
        [(name, field_type) for name, field_type, _mode in base_columns],
        [(name, field_type) for name, field_type, _mode in head_columns],
    )
    lines.extend(
        [
            f"added: {', '.join(added) or 'none'}",
            f"removed: {', '.join(removed) or 'none'}",
            f"type changes: {', '.join(changed) or 'none'}",
        ]
    )
    if skipped:
        lines.append(f"skipped columns: {', '.join(skipped)}")
    lines.append("")
    if profile is None:
        lines.append(profile_note)
    else:
        base_row = profile["base"]
        head_row = profile["head"]
        if base_row["n_rows"] == 0 and head_row["n_rows"] == 0:
            lines.append("rows: 0; no data to profile")
            columns = []
        elif base_row["n_rows"] == head_row["n_rows"]:
            lines.append("rows: no change")
        else:
            lines.append(f"rows: {base_row['n_rows']} → {head_row['n_rows']} ({delta(base_row['n_rows'], head_row['n_rows'])})")
        moved = [(name, field_type) for name, field_type in columns if column_moved(base_row, head_row, name, field_type)]
        unchanged = [name for name, field_type in columns if not column_moved(base_row, head_row, name, field_type)]
        if moved:
            rows = []
            for name, field_type in moved:
                nulls = (
                    f"{percentage(base_row[name + '__nulls'], base_row['n_rows'])} → "
                    f"{percentage(head_row[name + '__nulls'], head_row['n_rows'])}"
                )
                distincts = f"{int(base_row[name + '__distincts']):,} → {int(head_row[name + '__distincts']):,}"
                total = f"{base_row[name + '__sum']} → {head_row[name + '__sum']}" if field_type in NUMERIC_TYPES else ""
                rows.append((name, nulls, distincts, total))
            lines.extend(["", "Changed columns", *terminal_table(("Column", "Nulls", "Distincts", "Sum"), rows, {2, 3})])
        if unchanged:
            rows = [(name, f"{int(head_row[name + '__distincts']):,}") for name in unchanged]
            lines.extend(["", "Unchanged columns", *terminal_table(("Column", "Distincts"), rows, {1})])
    lines.append(f"\nOn-demand estimate at ${PRICE_PER_TIB:.2f}/TiB; no model is built.")
    return "\n".join(lines)
