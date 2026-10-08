"""OpenTollz: Illinois Tollway bid-item search, based on OpenBidz.
Run: solara run opentollz.py --host=0.0.0.0 --port=8765
Keep tollway_bids.parquet alongside this file.
"""
import os
from html import escape

import pandas as pd
import solara
import solara.lab

DATA_COLUMNS = [
    "Pay Item #", "Pay Item Description", "Unit", "Quantity", "Contract",
    "Unit Price Bid", "SOURCE FILE", "Date", "Project Description",
]


@solara.memoize
def load_bid_data():
    file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tollway_bids.parquet")
    df = pd.read_parquet(file_path)
    missing = [column for column in DATA_COLUMNS if column not in df.columns]
    if missing:
        raise ValueError(f"Tollway data is missing required columns: {', '.join(missing)}")
    df = df[DATA_COLUMNS].copy()
    for column in DATA_COLUMNS:
        if column not in {"Quantity", "Unit Price Bid", "Date"}:
            df[column] = df[column].fillna("").astype(str).str.strip()
    # Quantity and price arrive as strings; convert once for numeric filtering/sorting.
    for column in ("Quantity", "Unit Price Bid"):
        text = df[column].astype("string").str.replace(",", "", regex=False).str.replace("$", "", regex=False).str.strip()
        df[column] = pd.to_numeric(text, errors="coerce")
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce").dt.normalize()
    return df.set_index("Pay Item #")


df_all = load_bid_data()
sort_columns = DATA_COLUMNS.copy()
sort_orders = ["Ascending", "Descending"]
search_pay_item = solara.reactive("")
search_contract = solara.reactive("")
search_project_description = solara.reactive("")
min_quantity = solara.reactive(None)
max_quantity = solara.reactive(None)
start_date = solara.reactive(None)
end_date = solara.reactive(None)
chosen_sort_col = solara.reactive("Pay Item #")
chosen_sort_order = solara.reactive("Ascending")
current_page = solara.reactive(0)
ITEMS_PER_PAGE = 25


def clear_filters():
    """Reset all searches, filters, sorting, and pagination."""
    for value in (search_pay_item, search_contract, search_project_description):
        value.set("")
    for value in (min_quantity, max_quantity, start_date, end_date):
        value.set(None)
    chosen_sort_col.set("Pay Item #")
    chosen_sort_order.set("Ascending")
    current_page.set(0)


def set_dark_mode(enabled):
    solara.lab.theme.dark = bool(enabled)


def match_words(values, query):
    """All literal partial words must appear, regardless of order or case."""
    mask = pd.Series(True, index=values.index)
    for word in query.split():
        mask &= values.str.contains(word, case=False, regex=False, na=False)
    return mask


def filter_bid_data(frame, pay_item="", contract="", project_description="",
                    quantity_min=None, quantity_max=None, date_start=None, date_end=None,
                    sort_column="Pay Item #", sort_order="Ascending"):
    """Apply combined filters and stable sorting without modifying the source data."""
    filtered = frame
    if pay_item.strip():
        query = pay_item.strip()
        item_match = filtered.index.str.contains(query, case=False, regex=False, na=False)
        description_match = match_words(filtered["Pay Item Description"], query)
        filtered = filtered[item_match | description_match]
    if contract.strip():
        filtered = filtered[filtered["Contract"].str.contains(contract.strip(), case=False, regex=False, na=False)]
    if project_description.strip():
        filtered = filtered[match_words(filtered["Project Description"], project_description.strip())]
    if quantity_min is not None:
        filtered = filtered[filtered["Quantity"] >= quantity_min]
    if quantity_max is not None:
        filtered = filtered[filtered["Quantity"] <= quantity_max]
    if date_start is not None:
        filtered = filtered[filtered["Date"] >= pd.Timestamp(date_start).normalize()]
    if date_end is not None:
        filtered = filtered[filtered["Date"] <= pd.Timestamp(date_end).normalize()]
    ascending = sort_order == "Ascending"
    if sort_column == "Pay Item #":
        return filtered.sort_index(ascending=ascending, kind="mergesort")
    return filtered.sort_values(sort_column, ascending=ascending, kind="mergesort", na_position="last")


DISPLAY_HEADERS = {
    "SOURCE FILE": "Source File",
    "Date": "Letting Date",
    "Unit Price Bid": "Bid Unit Price",
}
PRICE_COLUMNS = {"Unit Price Bid"}
NUMERIC_COLUMNS = {"Quantity"} | PRICE_COLUMNS
CENTER_COLUMNS = {"Unit"}


def render_results_table(page_df):
    """One header per column, including Pay Item #, with matching cell alignment."""
    frame = page_df.reset_index()
    columns = frame.columns.tolist()

    def column_class(column):
        if column in NUMERIC_COLUMNS:
            return "bid-number"
        if column in CENTER_COLUMNS:
            return "bid-center"
        if column in {"Pay Item Description", "Project Description"}:
            return "bid-description"
        return "bid-text"

    headers = "".join(
        f'<th scope="col" class="{column_class(column)}">'
        f'{escape(DISPLAY_HEADERS.get(column, str(column)))}</th>'
        for column in columns
    )
    rows = []
    for values in frame.itertuples(index=False, name=None):
        cells = []
        for column, value in zip(columns, values):
            text = "" if pd.isna(value) else str(value)
            content = escape(text)
            if column == "Date" and text:
                content = escape(pd.Timestamp(value).strftime("%Y-%m-%d"))
            elif column in PRICE_COLUMNS and text:
                price = pd.to_numeric(value, errors="coerce")
                if pd.notna(price):
                    content = f"${price:,.2f}"
            cells.append(f'<td class="{column_class(column)}">{content}</td>')
        rows.append("<tr>" + "".join(cells) + "</tr>")
    return (
        '<table class="solara-custom-html-table" aria-label="Illinois Tollway bid item search results">'
        f'<thead><tr>{headers}</tr></thead><tbody>{"".join(rows)}</tbody></table>'
    )


@solara.component
def Page():
    solara.Title("Opentollz")
    selected_theme = solara.use_trait_observe(solara.lab.theme, "dark")
    effective_dark = solara.lab.use_dark_effective()
    dark_mode = bool(effective_dark if selected_theme is None else selected_theme)
    criteria = [
        search_pay_item.value, search_contract.value, search_project_description.value,
        min_quantity.value, max_quantity.value, start_date.value, end_date.value,
        chosen_sort_col.value, chosen_sort_order.value,
    ]
    solara.use_memo(lambda: current_page.set(0), dependencies=criteria)
    filtered_df = solara.use_memo(lambda: filter_bid_data(df_all, *criteria), dependencies=criteria)
    total_records = len(filtered_df)
    active_filter_count = sum(value is not None for value in criteria[3:7])
    filter_summary = "More Filters"
    if active_filter_count:
        filter_summary += f" ({active_filter_count} active)"

    # Keep the search controls within the viewport even when the table is wide.
    solara.Style("""
        .opentollz-app {
            width: 100%;
            max-width: 100%;
            min-width: 0;
            box-sizing: border-box;
            container-type: inline-size;
            container-name: opentollz;
        }
        .opentollz-panel {
            width: 100%;
            min-width: 0;
            max-width: 100%;
            box-sizing: border-box;
        }
        .opentollz-panel .v-card__text {
            min-width: 0;
            max-width: 100%;
            box-sizing: border-box;
        }
        .opentollz-app .opentollz-controls {
            display: grid !important;
            grid-template-columns: minmax(0, 1fr);
            gap: 12px;
            width: 100%;
            min-width: 0;
            max-width: 100%;
            box-sizing: border-box;
        }
        .opentollz-controls > * {
            min-width: 0;
            max-width: 100%;
        }
        @container opentollz (min-width: 900px) {
            .opentollz-app .opentollz-controls {
                grid-template-columns: repeat(2, minmax(0, 1fr));
            }
        }
        @container opentollz (min-width: 1200px) {
            .opentollz-app .opentollz-filter-controls {
                grid-template-columns: repeat(3, minmax(0, 1fr));
            }
        }
        .opentollz-panel .v-expansion-panel-header {
            min-height: 48px;
        }
        .opentollz-panel .v-expansion-panel-content__wrap {
            padding: 0 12px 12px;
        }
        .opentollz-results-scroll {
            display: block;
            width: 100%;
            max-width: 100%;
            min-width: 0;
            overflow-x: auto;
            -webkit-overflow-scrolling: touch;
            --bid-surface: #ffffff;
            --bid-header: #eef2f6;
            --bid-text: #243447;
            --bid-border: #d9e1ea;
            --bid-stripe: #f8fafc;
            --bid-hover: #edf4fc;
            --bid-link: #1565c0;
            border: 1px solid var(--bid-border);
            border-radius: 8px;
            background: var(--bid-surface);
        }
        .opentollz-results-scroll.opentollz-dark-table {
            --bid-surface: #1e1e1e;
            --bid-header: #2b3440;
            --bid-text: #e5eaf0;
            --bid-border: #46505c;
            --bid-stripe: #252a31;
            --bid-hover: #303e50;
            --bid-link: #90caf9;
        }
        .solara-custom-html-table {
            width: 100%;
            border-collapse: collapse;
            font-family: inherit;
            font-size: 14px;
            color: var(--bid-text);
            background: var(--bid-surface);
        }
        .solara-custom-html-table th,
        .solara-custom-html-table td {
            padding: 12px 14px;
            text-align: left;
            vertical-align: top;
            line-height: 1.5;
            border-bottom: 1px solid var(--bid-border);
        }
        .solara-custom-html-table thead th {
            background: var(--bid-header);
            color: var(--bid-text);
            font-weight: 700;
            vertical-align: middle;
            white-space: nowrap;
            border-bottom: 2px solid var(--bid-border);
        }
        .solara-custom-html-table .bid-number {
            text-align: right;
            font-variant-numeric: tabular-nums;
            white-space: nowrap;
        }
        .solara-custom-html-table .bid-center {
            text-align: center;
            white-space: nowrap;
        }
        .solara-custom-html-table .bid-text {
            white-space: nowrap;
        }
        .solara-custom-html-table .bid-description {
            min-width: 250px;
            max-width: 360px;
            white-space: normal;
            overflow-wrap: anywhere;
        }
        .solara-custom-html-table tbody tr:nth-child(even) {
            background: var(--bid-stripe);
        }
        .solara-custom-html-table tbody tr:hover {
            background: var(--bid-hover);
        }
        .solara-custom-html-table tbody tr:last-child td {
            border-bottom: 0;
        }
        .solara-custom-html-table a {
            color: var(--bid-link);
            font-weight: 600;
            text-decoration: underline;
        }
        @media (max-width: 599px) {
            .opentollz-panel {
                padding: 4px !important;
            }
            .opentollz-panel .v-card__title {
                font-size: 1.15rem;
                line-height: 1.4;
                overflow-wrap: anywhere;
            }
            .opentollz-panel .v-card__text {
                padding: 12px;
            }
            .opentollz-panel .v-btn {
                min-height: 44px;
            }
        }
    """)

    with solara.Column(classes=["opentollz-app"], gap="12px"):
        with solara.Row(justify="end", style="padding: 0 12px; min-height: 48px;"):
            solara.Switch(label="Dark Mode", value=dark_mode, on_value=set_dark_mode)

        # ---- COMPACT RESPONSIVE SEARCH PANEL ----
        with solara.Card(
            "Search Illinois Tollway Bid Items",
            classes=["opentollz-panel"], margin=0,
            style="margin-bottom: 16px; padding: 12px;",
        ):
            solara.Markdown(
                "Search by pay item, description, contract number, or project description.",
                style="margin-bottom: 8px;",
            )
            with solara.Column(classes=["opentollz-controls"], gap="12px"):
                solara.InputText("Pay Item # or Description", value=search_pay_item)
                solara.InputText("Contract Number", value=search_contract)
                solara.InputText(
                    "Project Description", value=search_project_description,
                    message="Partial words, any order; all words must match. Press Enter or leave the field to search.",
                )

            with solara.ColumnsResponsive(default=12, small=12, medium=[8, 4], gutters_dense=True):
                solara.Markdown(
                    f"**{total_records:,}** matching bid item records",
                    style="margin: 8px 0;",
                )
                solara.Button(label="Clear Filters", on_click=clear_filters)

            # Additional filters and sorting start collapsed.
            with solara.Details(summary=filter_summary, expand=False):
                with solara.Column(classes=["opentollz-controls", "opentollz-filter-controls"], gap="12px"):
                    solara.InputFloat(label="Min Quantity", value=min_quantity, optional=True, clearable=True)
                    solara.InputFloat(label="Max Quantity", value=max_quantity, optional=True, clearable=True)
                    solara.lab.InputDate(label="Start Letting Date", value=start_date, optional=True, clearable=True)
                    solara.lab.InputDate(label="End Letting Date", value=end_date, optional=True, clearable=True)

            with solara.Details(summary="Sort Options", expand=False):
                with solara.Column(classes=["opentollz-controls"], gap="12px"):
                    solara.Select(label="Sort By", value=chosen_sort_col, values=sort_columns)
                    solara.Select(label="Direction", value=chosen_sort_order, values=sort_orders)

        # ---- MAIN DATAFRAME RESULTS PANEL ----
        with solara.Card(
            "Illinois Tollway Bid Items Search Results",
            classes=["opentollz-panel"], margin=0,
            style="padding: 12px;",
        ):
            display_df = filtered_df

            # ---- DYNAMIC CSV FILE DOWNLOAD COMPONENT ----
            with solara.ColumnsResponsive(default=12, small=12, medium=[8, 4], gutters_dense=True):
                solara.Markdown(f"### Found **{total_records:,}** matching bid item records.")

                if total_records > 0:
                    solara.FileDownload(
                        data=lambda: display_df.to_csv(index=True),
                        filename="filtered_tollway_bid_results.csv",
                        label="📥 Download as CSV",
                        mime_type="text/csv"
                    )

            # Render formatted grid or error state
            if total_records > 0:
                max_pages = (total_records - 1) // ITEMS_PER_PAGE
                page_index = min(current_page.value, max_pages)
                start_idx = page_index * ITEMS_PER_PAGE
                end_idx = start_idx + ITEMS_PER_PAGE
                sliced_df = display_df.iloc[start_idx:end_idx].copy()

                html_table = render_results_table(sliced_df)
                table_classes = ["opentollz-results-scroll"]
                if dark_mode:
                    table_classes.append("opentollz-dark-table")
                solara.HTML(
                    tag="div",
                    classes=table_classes,
                    unsafe_innerHTML=html_table,
                    attributes={"tabindex": "0", "aria-label": "Scroll bid item results horizontally"},
                )

                # ---- NATIVE PAGINATION INTERFACE BAR ----
                with solara.Row(justify="center", style="margin-top: 15px; align-items: center; flex-wrap: wrap; gap: 8px;"):
                    solara.Button(
                        label="◀ Previous", 
                        disabled=page_index == 0, 
                        on_click=lambda: current_page.set(max(0, page_index - 1))
                    )
                    solara.Markdown(f"**Page {page_index + 1} of {max_pages + 1}**", style="margin: 0 15px;")
                    solara.Button(
                        label="Next ▶", 
                        disabled=page_index >= max_pages, 
                        on_click=lambda: current_page.set(min(max_pages, page_index + 1))
                    )
            else:
                solara.Error("No records found matching current filtering combinations.")
