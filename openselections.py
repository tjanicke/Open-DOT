"""OpenSelections: IDOT consultant-selection search.

Place ptb_selection_data.parquet beside this file, then run:
    solara run openselections.py --host 127.0.0.1 --port 8766

Dependencies: solara, pandas, pyarrow (same as OpenBidz).
"""
from pathlib import Path
from html import escape

import pandas as pd
import solara
import solara.lab
import ipyvuetify as vuetify
import traitlets


DATA_FILENAME = "ptb_selection_data.parquet"
ITEMS_PER_PAGE = 25
ALL = "All"
FIRM_COLUMNS = [
    "Selected Firm", "Proposed Subconsultants", "First Alternate", "Second Alternate",
]
FIRM_ROLES = ["Selected Firm", "Any Firm Role"] + FIRM_COLUMNS[1:]
NUMERIC_COLUMNS = {
    "PTB Number", "Item Number", "PTB Bulletin Year", "Firms Submitted", "Firms Eligible",
}
# Keep the project beside its selected firm; all source columns remain available.
DISPLAY_COLUMNS = [
    "PTB Number", "Item Number", "PTB Bulletin Year", "Job Number",
    "Selected Firm", "Project Description", "District", "County",
    "Proposed Subconsultants", "First Alternate", "Second Alternate",
    "Firms Submitted", "Firms Eligible", "Source File",
]
DISPLAY_HEADERS = {
    "PTB Number": "PTB #",
    "Item Number": "Item #",
    "PTB Bulletin Year": "Bulletin Year",
}


@solara.memoize
def load_selection_data():
    frame = pd.read_parquet(Path(__file__).resolve().parent / DATA_FILENAME)
    # The supplied file has a trailing space in "District ".
    frame.columns = frame.columns.str.strip()
    missing = set(DISPLAY_COLUMNS) - set(frame.columns)
    if missing:
        raise ValueError("Missing required selection columns: " + ", ".join(sorted(missing)))
    for column in frame.columns:
        if pd.api.types.is_object_dtype(frame[column]) or pd.api.types.is_string_dtype(frame[column]):
            frame[column] = frame[column].astype("string").str.strip().replace("", pd.NA)
    return frame


df_all = load_selection_data()


def filter_options(column, *, numeric=False, descending=False):
    values = df_all[column].dropna().astype(str).unique().tolist()
    if numeric:
        values.sort(key=lambda value: (float(value), value), reverse=descending)
    else:
        values.sort(key=str.casefold)
    return [ALL] + values


available_counties = filter_options("County")
available_districts = filter_options("District")
available_ptbs = filter_options("PTB Number", numeric=True, descending=True)
available_years = filter_options("PTB Bulletin Year", numeric=True)
sort_columns = DISPLAY_COLUMNS
sort_orders = ["Ascending", "Descending"]

search_firm = solara.reactive("")
search_project = solara.reactive("")
selected_county = solara.reactive(ALL)
selected_district = solara.reactive(ALL)
selected_ptb = solara.reactive(ALL)
selected_beginning_year = solara.reactive(ALL)
selected_ending_year = solara.reactive(ALL)
selected_firm_role = solara.reactive("Selected Firm")
chosen_sort_col = solara.reactive("PTB Number")
chosen_sort_order = solara.reactive("Descending")
current_page = solara.reactive(0)


def clear_filters():
    search_firm.set("")
    search_project.set("")
    selected_county.set(ALL)
    selected_district.set(ALL)
    selected_ptb.set(ALL)
    selected_beginning_year.set(ALL)
    selected_ending_year.set(ALL)
    selected_firm_role.set("Selected Firm")
    chosen_sort_col.set("PTB Number")
    chosen_sort_order.set("Descending")
    current_page.set(0)


def set_dark_mode(enabled):
    solara.lab.theme.dark = bool(enabled)


def filter_selection_data(
    frame, *, firm_query="", project_query="", county=ALL, district=ALL,
    ptb=ALL, beginning_year=ALL, ending_year=ALL, firm_role="Selected Firm",
    sort_column="PTB Number", sort_order="Descending",
):
    """Filter literal, case-insensitive searches and sort numbers numerically."""
    result = frame
    for column, value in [
        ("County", county), ("District", district), ("PTB Number", ptb),
    ]:
        if value != ALL:
            result = result[result[column] == value]

    # Include both endpoints; All leaves that side of the range unrestricted.
    if beginning_year != ALL or ending_year != ALL:
        years = pd.to_numeric(result["PTB Bulletin Year"], errors="coerce")
        keep = pd.Series(True, index=result.index)
        if beginning_year != ALL:
            keep &= years >= int(beginning_year)
        if ending_year != ALL:
            keep &= years <= int(ending_year)
        result = result[keep]

    if firm_query.strip():
        columns = FIRM_COLUMNS if firm_role == "Any Firm Role" else [firm_role]
        # Each keyword must appear within the same firm's role field.
        role_matches = pd.Series(False, index=result.index)
        for column in columns:
            field_match = pd.Series(True, index=result.index)
            for keyword in firm_query.split():
                field_match &= result[column].str.contains(keyword, case=False, regex=False, na=False)
            role_matches |= field_match
        result = result[role_matches]

    if project_query.strip():
        query = project_query.strip()
        job_match = result["Job Number"].str.contains(query, case=False, regex=False, na=False)
        description_match = pd.Series(True, index=result.index)
        for keyword in query.split():
            description_match &= result["Project Description"].str.contains(
                keyword, case=False, regex=False, na=False,
            )
        result = result[job_match | description_match]

    ascending = sort_order == "Ascending"
    columns, directions = [sort_column], [ascending]
    for column, direction in [("PTB Number", False), ("Item Number", True)]:
        if column not in columns:
            columns.append(column)
            directions.append(direction)

    def sort_key(series):
        if series.name in NUMERIC_COLUMNS:
            return pd.to_numeric(series, errors="coerce")
        return series.astype("string").str.casefold()

    return result.sort_values(
        by=columns, ascending=directions, key=sort_key, na_position="last", kind="stable",
    )


def render_results_table(frame):
    def column_class(column):
        if column in NUMERIC_COLUMNS:
            return "selection-number"
        if column == "Project Description":
            return "selection-description"
        if column == "Proposed Subconsultants":
            return "selection-longtext"
        if column in FIRM_COLUMNS:
            return "selection-firm"
        if column in {"County", "District"}:
            return "selection-region"
        return "selection-text"

    headers = "".join(
        f'<th scope="col" class="{column_class(column)}">'
        f'{escape(DISPLAY_HEADERS.get(column, column))}</th>'
        for column in frame.columns
    )
    rows = []
    for values in frame.itertuples(index=False, name=None):
        cells = []
        for column, value in zip(frame.columns, values):
            text = "" if pd.isna(value) else str(value)
            cells.append(f'<td class="{column_class(column)}">{escape(text)}</td>')
        rows.append("<tr>" + "".join(cells) + "</tr>")
    return (
        '<table class="openselections-table" aria-label="IDOT consultant selection search results">'
        f'<thead><tr>{headers}</tr></thead><tbody>{"".join(rows)}</tbody></table>'
    )


# Same responsive spacing, header alignment, row shading, and theme as OpenBidz.
APP_CSS = """
        .openselections-app {
            width: 100%;
            max-width: 100%;
            min-width: 0;
            box-sizing: border-box;
            container-type: inline-size;
            container-name: openselections;
        }
        .openselections-panel {
            width: 100%;
            min-width: 0;
            max-width: 100%;
            box-sizing: border-box;
        }
        .openselections-panel .v-card__text {
            min-width: 0;
            max-width: 100%;
            box-sizing: border-box;
        }
        .openselections-app .openselections-controls {
            display: grid !important;
            grid-template-columns: minmax(0, 1fr);
            gap: 12px;
            width: 100%;
            min-width: 0;
            max-width: 100%;
            box-sizing: border-box;
        }
        .openselections-controls > * {
            min-width: 0;
            max-width: 100%;
        }
        @container openselections (min-width: 900px) {
            .openselections-app .openselections-controls {
                grid-template-columns: repeat(2, minmax(0, 1fr));
            }
        }
        @container openselections (min-width: 1200px) {
            .openselections-app .openselections-filter-controls {
                grid-template-columns: repeat(3, minmax(0, 1fr));
            }
        }
        .openselections-panel .v-expansion-panel-header {
            min-height: 48px;
        }
        .openselections-panel .v-expansion-panel-content__wrap {
            padding: 0 12px 12px;
        }
        .openselections-table-frame {
            width: 100%;
            max-width: 100%;
            min-width: 0;
            box-sizing: border-box;
            --selection-surface: #ffffff;
            --selection-header: #eef2f6;
            --selection-text: #243447;
            --selection-border: #d9e1ea;
            --selection-stripe: #f8fafc;
            --selection-hover: #edf4fc;
            --selection-link: #1565c0;
        }
        .openselections-table-frame.openselections-dark-table {
            --selection-surface: #1e1e1e;
            --selection-header: #2b3440;
            --selection-text: #e5eaf0;
            --selection-border: #46505c;
            --selection-stripe: #252a31;
            --selection-hover: #303e50;
            --selection-link: #90caf9;
        }
        .openselections-results-scroll,
        .openselections-top-scroll {
            display: block;
            width: 100%;
            max-width: 100%;
            min-width: 0;
            box-sizing: border-box;
            overflow-x: scroll;
            overflow-y: hidden;
            scrollbar-width: auto;
            scrollbar-color: #8493a5 var(--selection-header);
            -webkit-overflow-scrolling: touch;
            border: 1px solid var(--selection-border);
            border-radius: 8px;
            background: var(--selection-surface);
        }
        .openselections-top-scroll {
            height: 20px;
            margin-bottom: 8px;
        }
        .openselections-table-frame ::-webkit-scrollbar {
            height: 14px;
        }
        .openselections-table-frame ::-webkit-scrollbar-track {
            background: var(--selection-header);
        }
        .openselections-table-frame ::-webkit-scrollbar-thumb {
            background: #8493a5;
            border: 3px solid var(--selection-header);
            border-radius: 7px;
        }
        .openselections-scroll-hint {
            margin: 0 0 6px;
            font-size: 13px;
            color: var(--selection-text);
        }
        .openselections-table {
            width: 100%;
            border-collapse: collapse;
            font-family: inherit;
            font-size: 14px;
            color: var(--selection-text);
            background: var(--selection-surface);
        }
        .openselections-table th,
        .openselections-table td {
            padding: 12px 14px;
            text-align: left;
            vertical-align: top;
            line-height: 1.5;
            border-bottom: 1px solid var(--selection-border);
        }
        .openselections-table thead th {
            background: var(--selection-header);
            color: var(--selection-text);
            font-weight: 700;
            vertical-align: middle;
            white-space: nowrap;
            border-bottom: 2px solid var(--selection-border);
        }
        .openselections-table .selection-number {
            text-align: right;
            font-variant-numeric: tabular-nums;
            white-space: nowrap;
        }
        .openselections-table .selection-center {
            text-align: center;
            white-space: nowrap;
        }
        .openselections-table .selection-text {
            white-space: nowrap;
        }
        .openselections-table .selection-description {
            min-width: 250px;
            max-width: 360px;
            white-space: normal;
            overflow-wrap: anywhere;
        }
        .openselections-table tbody tr:nth-child(even) {
            background: var(--selection-stripe);
        }
        .openselections-table tbody tr:hover {
            background: var(--selection-hover);
        }
        .openselections-table tbody tr:last-child td {
            border-bottom: 0;
        }
        .openselections-table a {
            color: var(--selection-link);
            font-weight: 600;
            text-decoration: underline;
        }
        @media (max-width: 599px) {
            .openselections-panel {
                padding: 4px !important;
            }
            .openselections-panel .v-card__title {
                font-size: 1.15rem;
                line-height: 1.4;
                overflow-wrap: anywhere;
            }
            .openselections-panel .v-card__text {
                padding: 12px;
            }
            .openselections-panel .v-btn {
                min-height: 44px;
            }
        }
    
        .openselections-table .selection-firm {
            min-width: 200px;
            max-width: 300px;
            white-space: normal;
            overflow-wrap: anywhere;
        }
        .openselections-table .selection-longtext {
            min-width: 260px;
            max-width: 380px;
            white-space: normal;
            overflow-wrap: anywhere;
        }
        .openselections-table .selection-region {
            min-width: 130px;
            max-width: 240px;
            white-space: normal;
            overflow-wrap: anywhere;
        }
"""


class SelectionTable(vuetify.VuetifyTemplate):
    """Native horizontal scroll areas synchronized entirely in the browser."""
    table_html = traitlets.Unicode("").tag(sync=True)
    dark = traitlets.Bool(False).tag(sync=True)

    @traitlets.default("template")
    def _table_template(self):
        return """
        <template>
          <div class="openselections-table-frame"
               :class="{ 'openselections-dark-table': dark }">
            <p v-show="hasOverflow" class="openselections-scroll-hint">
              Scroll sideways to view all columns.
            </p>
            <div v-show="hasOverflow" ref="top"
                 class="openselections-top-scroll" tabindex="0"
                 role="region" aria-label="Horizontal table scrollbar"
                 @scroll="scrollFromTop">
              <div ref="spacer" style="height: 1px;"></div>
            </div>
            <div ref="bottom" class="openselections-results-scroll" tabindex="0"
                 role="region" aria-label="Scroll selection results horizontally"
                 @scroll="scrollFromBottom" v-html="table_html">
            </div>
          </div>
        </template>
        <script>
        export default {
          data() {
            return { hasOverflow: true };
          },
          mounted() {
            this.$nextTick(this.measure);
            if (typeof ResizeObserver !== 'undefined') {
              this._tableResizeObserver = new ResizeObserver(() => this.measure());
              this._tableResizeObserver.observe(this.$refs.bottom);
            }
            window.addEventListener('resize', this.measure);
          },
          watch: {
            table_html() { this.$nextTick(this.measure); },
            dark() { this.$nextTick(this.measure); }
          },
          methods: {
            measure() {
              const bottom = this.$refs.bottom;
              const top = this.$refs.top;
              const spacer = this.$refs.spacer;
              if (!bottom || !top || !spacer) return;
              // Equal client widths keep both scroll ranges identical.
              spacer.style.width = bottom.scrollWidth + 'px';
              this.hasOverflow = bottom.scrollWidth > bottom.clientWidth + 1;
              top.scrollLeft = bottom.scrollLeft;
            },
            scrollFromTop() {
              const top = this.$refs.top;
              const bottom = this.$refs.bottom;
              if (top && bottom && bottom.scrollLeft !== top.scrollLeft) {
                bottom.scrollLeft = top.scrollLeft;
              }
            },
            scrollFromBottom() {
              const top = this.$refs.top;
              const bottom = this.$refs.bottom;
              if (top && bottom && top.scrollLeft !== bottom.scrollLeft) {
                top.scrollLeft = bottom.scrollLeft;
              }
            },
            cleanup() {
              if (this._tableResizeObserver) this._tableResizeObserver.disconnect();
              window.removeEventListener('resize', this.measure);
            }
          },
          beforeDestroy() { this.cleanup(); },
          beforeUnmount() { this.cleanup(); }
        };
        </script>
        """


@solara.component
def Page():
    solara.Title("OpenSelections")
    selected_theme = solara.use_trait_observe(solara.lab.theme, "dark")
    effective_dark = solara.lab.use_dark_effective()
    dark_mode = bool(effective_dark if selected_theme is None else selected_theme)

    filter_dependencies = [
        search_firm.value, search_project.value, selected_county.value,
        selected_district.value, selected_ptb.value,
        selected_beginning_year.value, selected_ending_year.value,
        selected_firm_role.value, chosen_sort_col.value, chosen_sort_order.value,
    ]
    solara.use_memo(lambda: current_page.set(0), dependencies=filter_dependencies)
    filtered_df = solara.use_memo(
        lambda: filter_selection_data(
            df_all, firm_query=search_firm.value, project_query=search_project.value,
            county=selected_county.value, district=selected_district.value,
            ptb=selected_ptb.value,
            beginning_year=selected_beginning_year.value, ending_year=selected_ending_year.value,
            firm_role=selected_firm_role.value, sort_column=chosen_sort_col.value,
            sort_order=chosen_sort_order.value,
        ),
        dependencies=filter_dependencies,
    )
    display_df = filtered_df[DISPLAY_COLUMNS]
    total_records = len(display_df)
    active_count = sum([
        selected_county.value != ALL, selected_district.value != ALL,
        selected_ptb.value != ALL,
        selected_beginning_year.value != ALL or selected_ending_year.value != ALL,
        selected_firm_role.value != "Selected Firm",
    ])
    filter_summary = "More Filters" + (f" ({active_count} active)" if active_count else "")

    solara.Style(APP_CSS)
    with solara.Column(classes=["openselections-app"], gap="12px"):
        with solara.Row(justify="end", style="padding: 0 12px; min-height: 48px;"):
            solara.Switch(label="Dark Mode", value=dark_mode, on_value=set_dark_mode)

        with solara.Card(
            "Search IDOT Consultant Selections",
            classes=["openselections-panel"], margin=0,
            style="margin-bottom: 16px; padding: 12px;",
        ):
            solara.Markdown(
                "Search selected firms, project descriptions, or job numbers.",
                style="margin-bottom: 8px;",
            )
            with solara.Column(classes=["openselections-controls"], gap="12px"):
                solara.InputText("Firm Name", value=search_firm)
                solara.InputText("Project Description or Job Number", value=search_project)
            with solara.ColumnsResponsive(default=12, small=12, medium=[8, 4], gutters_dense=True):
                solara.Markdown(f"**{total_records:,}** matching selection records", style="margin: 8px 0;")
                solara.Button(label="Clear Filters", on_click=clear_filters)

            with solara.Details(summary=filter_summary, expand=False):
                solara.Markdown(
                    "Year ranges include both endpoints; All leaves an endpoint open. "
                    "Choose a firm role to search subconsultants or alternates.",
                    style="margin-bottom: 8px;",
                )
                with solara.Column(classes=["openselections-controls", "openselections-filter-controls"], gap="12px"):
                    solara.Select(label="PTB Number", value=selected_ptb, values=available_ptbs)
                    solara.Select(label="Beginning Year", value=selected_beginning_year, values=available_years)
                    solara.Select(label="Ending Year", value=selected_ending_year, values=available_years)
                    solara.Select(label="District / Bureau", value=selected_district, values=available_districts)
                    solara.Select(label="County", value=selected_county, values=available_counties)
                    solara.Select(label="Firm Role", value=selected_firm_role, values=FIRM_ROLES)

                if selected_beginning_year.value != ALL and selected_ending_year.value != ALL:
                    if int(selected_beginning_year.value) > int(selected_ending_year.value):
                        solara.Warning("Beginning Year must be earlier than or equal to Ending Year.")

            with solara.Details(summary="Sort Options", expand=False):
                with solara.Column(classes=["openselections-controls"], gap="12px"):
                    solara.Select(label="Sort By", value=chosen_sort_col, values=sort_columns)
                    solara.Select(label="Direction", value=chosen_sort_order, values=sort_orders)

        with solara.Card(
            "IDOT Consultant Selection Search Results",
            classes=["openselections-panel"], margin=0, style="padding: 12px;",
        ):
            with solara.ColumnsResponsive(default=12, small=12, medium=[8, 4], gutters_dense=True):
                solara.Markdown(f"### Found **{total_records:,}** matching selection records.")
                if total_records:
                    solara.FileDownload(
                        data=display_df.to_csv(index=False),
                        filename="filtered_selection_results.csv",
                        label="📥 Download as CSV", mime_type="text/csv",
                    )

            if total_records:
                max_page = (total_records - 1) // ITEMS_PER_PAGE
                page_index = min(current_page.value, max_page)
                start = page_index * ITEMS_PER_PAGE
                SelectionTable.element(
                    table_html=render_results_table(display_df.iloc[start:start + ITEMS_PER_PAGE]),
                    dark=dark_mode,
                )
                with solara.Row(
                    justify="center",
                    style="margin-top: 15px; align-items: center; flex-wrap: wrap; gap: 8px;",
                ):
                    solara.Button(
                        label="◀ Previous", disabled=page_index == 0,
                        on_click=lambda: current_page.set(max(0, current_page.value - 1)),
                    )
                    solara.Markdown(
                        f"**Page {page_index + 1} of {max_page + 1}**", style="margin: 0 15px;",
                    )
                    solara.Button(
                        label="Next ▶", disabled=page_index >= max_page,
                        on_click=lambda: current_page.set(min(max_page, current_page.value + 1)),
                    )
            else:
                solara.Info("No records match these searches and filters. Try fewer filters or Clear Filters.")
