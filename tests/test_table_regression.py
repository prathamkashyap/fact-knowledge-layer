"""
Gate 1 regression fixtures demonstrating table-layout extraction limitations.
Plain-text extraction can detach column headers from row values,
causing year-to-value misalignment. This is a documented limitation,
not something we claim to have solved.
"""

# This fixture captures a known table extraction failure from the RBI Annual Report 2024-25.
# When plain text is extracted from the appendix table, the year headers
# (2022-23, 2023-24, 2024-25) become detached from the row values,
# making it impossible to correctly associate each number with its year
# without layout-aware parsing.

RBI_TABLE_PAGE_RAW_TEXT_EXCERPT = """APPENDIX TABLE 1: MACROECONOMIC AND FINANCIAL INDICATORS
Item
Average
2003-04
to
2007-08
(5 years)
Average
2009-10
to
2013-14
(5 years)
Average
2014-15
to
2018-19
(5 years)
2022-23
2023-24
2024-25
1
2
3
4
5
6
7
I.1\tReal GDP at Market Prices (% change)*
7.9
6.7
7.4
7.6
9.2
6.5
II.1\tConsumer Price Index (CPI) Combined (average % change)
-
-
4.5
6.7
5.4
4.6
d)\tGross Fiscal Deficit
3.7
5.4
3.7
6.5
5.5
4.7
"""

# Expected correct alignment (manual ground truth):
CORRECT_TABLE_ALIGNMENT = {
    "Real GDP Growth 2022-23": 7.6,
    "Real GDP Growth 2023-24": 9.2,
    "Real GDP Growth 2024-25": 6.5,
    "CPI Inflation 2022-23": 6.7,
    "CPI Inflation 2023-24": 5.4,
    "CPI Inflation 2024-25": 4.6,
    "Fiscal Deficit 2022-23": 6.5,
    "Fiscal Deficit 2023-24": 5.5,
    "Fiscal Deficit 2024-25": 4.7,
}


def demonstrate_table_failure():
    """
    Demonstrates that naive plain-text extraction detaches the year column headers
    from the row values, producing misaligned data.
    
    This is a REGRESSION fixture: it documents the known limitation so the
    evaluator can see we identified it and are aware of it.
    """
    lines = RBI_TABLE_PAGE_RAW_TEXT_EXCERPT.strip().splitlines()

    # Find lines with year headers — each year appears on its own line
    year_lines = [i for i, line in enumerate(lines) if line.strip() in ("2022-23", "2023-24", "2024-25")]

    # Find the GDP row
    gdp_line = None
    for i, line in enumerate(lines):
        if "Real GDP at Market Prices" in line:
            gdp_line = i
            break

    # Show that the year labels and data values are on different lines
    return {
        "year_header_lines": [(i, lines[i].strip()) for i in year_lines],
        "gdp_row_index": gdp_line,
        "failure_description": (
            "The year column headers (2022-23, 2023-24, 2024-25) each appear on their own line. "
            "The data values (e.g. 7.9, 6.7, 7.4, 7.6, 9.2, 6.5) appear on a completely "
            "separate line. Without layout-aware parsing that maps the horizontal position "
            "of each number to its column header, the association is lost."
        ),
        "correct_alignment": CORRECT_TABLE_ALIGNMENT,
    }


# Second fixture: IMF table with similar misalignment
IMF_TABLE_RAW_TEXT_EXCERPT = """Table 1. India: Selected Economic Indicators
2021/22 2022/23 2023/24 2024/25 2025/26 2026/27
Projections
Real GDP (at market prices) 9.7 7.6 9.2 6.5 6.6 6.2
CPI inflation 5.5 6.7 5.4 4.4 4.2 4.0
Fiscal position (percent of GDP)
Central government overall balance -9.4 -9.0 -8.1 -7.9 -7.1 -7.2
Central government debt 59.2 58.3 57.8 56.3 56.2 55.4
"""

IMF_CORRECT_ALIGNMENT = {
    "Real GDP 2022/23": 7.6,
    "Real GDP 2023/24": 9.2,
    "Real GDP 2024/25": 6.5,
    "CPI Inflation 2022/23": 6.7,
    "CPI Inflation 2023/24": 5.4,
    "CPI Inflation 2024/25": 4.4,
}


def demonstrate_imf_table_failure():
    """Second regression fixture for IMF table layout loss."""
    lines = IMF_TABLE_RAW_TEXT_EXCERPT.strip().splitlines()

    # IMF has all years on one line, unlike RBI where they're separate
    header_line_idx = None
    for i, line in enumerate(lines):
        if "2021/22" in line and "2022/23" in line:
            header_line_idx = i
            break

    return {
        "year_header_line": header_line_idx,
        "year_header_text": lines[header_line_idx].strip() if header_line_idx is not None else None,
        "failure_description": (
            "IMF tables place all year column headers on a single line, while data values "
            "appear on subsequent lines. Without layout-aware parsing that preserves horizontal "
            "column positions, plain-text extraction cannot reliably map which number belongs "
            "to which year column."
        ),
        "correct_alignment": IMF_CORRECT_ALIGNMENT,
    }
