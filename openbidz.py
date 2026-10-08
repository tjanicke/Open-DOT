import os
import re
import datetime as dt
from html import escape
from urllib.parse import urlsplit
import pandas as pd
import solara
import solara.lab  # Required for lab components like InputDate


# ==========================================
# STEP 1: DEFINE YOUR DATA BACKEND (PARQUET)
# ==========================================
@solara.memoize
def load_bid_data():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    file_path = os.path.join(base_dir, "idot_bids.parquet")

    df = pd.read_parquet(file_path)

    df['Pay Item #'] = df['Pay Item #'].astype(str).str.strip()
    df['County'] = df['County'].astype(str).str.strip()
    df['Dist'] = df['Dist'].astype(str).str.strip()

    # Strip out trailing timestamps from the raw data safely
    if "Date" in df.columns:
        df["Date"] = df["Date"].astype(str).str.split(" ").str.get(0).str.strip()

    df = df.set_index('Pay Item #')
    return df


df_all = load_bid_data()

# ==========================================
# STEP 2: SET UP REACTIVE FILTERS
# ==========================================
available_counties = ["All"] + sorted(df_all["County"].dropna().unique().tolist())
available_districts = ["Any"] + sorted(df_all["Dist"].dropna().unique().tolist())
awarded_options = ["Any", "Yes", "No"]

sort_columns = ["Pay Item #", "Pay Item Description", "Unit", "Quantity", "Contract", "AWARDED", "County",
                "SOURCE FILE", "Date", "Eplan_Link"]
sort_orders = ["Ascending", "Descending"]

search_pay_item = solara.reactive("")
search_contract = solara.reactive("")
selected_county = solara.reactive("All")
selected_district = solara.reactive("Any")
selected_awarded = solara.reactive("Any")
min_quantity = solara.reactive(None)
max_quantity = solara.reactive(None)
start_date = solara.reactive(None)
end_date = solara.reactive(None)

chosen_sort_col = solara.reactive("Pay Item #")
chosen_sort_order = solara.reactive("Ascending")

# PERFORMANCE ADDITION: Current active page state
current_page = solara.reactive(0)
ITEMS_PER_PAGE = 25


def clear_filters():
    """Reset all searches, filters, sorting, and pagination."""
    search_pay_item.set("")
    search_contract.set("")
    selected_county.set("All")
    selected_district.set("Any")
    selected_awarded.set("Any")
    min_quantity.set(None)
    max_quantity.set(None)
    start_date.set(None)
    end_date.set(None)
    chosen_sort_col.set("Pay Item #")
    chosen_sort_order.set("Ascending")
    current_page.set(0)


def set_dark_mode(enabled):
    """Apply Solara's native theme to this visitor's app session."""
    solara.lab.theme.dark = bool(enabled)


DISPLAY_HEADERS = {
    "Dist": "District",
    "AWARDED": "Awarded",
    "SOURCE FILE": "Source File",
    "Date": "Letting Date",
    "FOLDER": "Folder",
    "Eplan_Link": "E-Plans",
    "Unit Price Bid": "Bid Unit Price",
}
PRICE_COLUMNS = {"Unit Price Bid", "Award Unit Price"}
NUMERIC_COLUMNS = {"Quantity"} | PRICE_COLUMNS
CENTER_COLUMNS = {"Unit", "Dist", "AWARDED"}


def render_results_table(page_df):
    """One header per column, including Pay Item #, with matching cell alignment."""
    frame = page_df.reset_index()
    columns = frame.columns.tolist()

    def column_class(column):
        if column in NUMERIC_COLUMNS:
            return "bid-number"
        if column in CENTER_COLUMNS:
            return "bid-center"
        if column == "Pay Item Description":
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
            if column in PRICE_COLUMNS and text:
                price = pd.to_numeric(value, errors="coerce")
                if pd.notna(price):
                    content = f"${price:,.2f}"
            elif column == "Eplan_Link":
                url = text.strip()
                parsed = urlsplit(url)
                if parsed.scheme.lower() in {"http", "https"} and parsed.netloc:
                    content = (
                        f'<a href="{escape(url, quote=True)}" target="_blank" '
                        'rel="noopener noreferrer">Go to E-Plans</a>'
                    )
                else:
                    content = "No Link"
            cells.append(f'<td class="{column_class(column)}">{content}</td>')
        rows.append("<tr>" + "".join(cells) + "</tr>")
    return (
        '<table class="solara-custom-html-table" aria-label="IDOT bid item search results">'
        f'<thead><tr>{headers}</tr></thead><tbody>{"".join(rows)}</tbody></table>'
    )


# ==========================================
# STEP 3: WRITE THE INTERFACE
# ==========================================
@solara.component
def Page():
    selected_theme = solara.use_trait_observe(solara.lab.theme, "dark")
    effective_dark = solara.lab.use_dark_effective()
    dark_mode = bool(effective_dark if selected_theme is None else selected_theme)

    # Reset page index to 0 whenever search queries or filters alter dataset bounds
    # This prevents pagination indexing ghost crashes
    solara.use_memo(
        lambda: current_page.set(0),
        dependencies=[search_pay_item.value, search_contract.value, selected_county.value,
                      selected_district.value, selected_awarded.value, min_quantity.value,
                      max_quantity.value, start_date.value, end_date.value, chosen_sort_col.value,
                      chosen_sort_order.value]
    )

    filtered_df = df_all.copy()

    # Apply reactive structural criteria
    if selected_county.value != "All":
        filtered_df = filtered_df[filtered_df["County"] == selected_county.value]

    if selected_district.value != "Any":
        filtered_df = filtered_df[filtered_df["Dist"] == selected_district.value]

    # Map human-readable "Yes"/"No" down to the required "Y"/"N" database flags
    if selected_awarded.value != "Any":
        db_flag = "Y" if selected_awarded.value == "Yes" else "N"
        filtered_df = filtered_df[filtered_df["AWARDED"].str.upper() == db_flag]

    # Apply Quantity Boundary Filtering
    if min_quantity.value is not None or max_quantity.value is not None:
        numeric_qty = pd.to_numeric(filtered_df["Quantity"], errors="coerce")
        if min_quantity.value is not None:
            filtered_df = filtered_df[numeric_qty >= min_quantity.value]
            numeric_qty = pd.to_numeric(filtered_df["Quantity"], errors="coerce")
        if max_quantity.value is not None:
            filtered_df = filtered_df[numeric_qty <= max_quantity.value]

    # Apply Letting Date Boundary Filtering
    if start_date.value is not None or end_date.value is not None:
        datetime_col = pd.to_datetime(filtered_df["Date"], errors="coerce")

        if start_date.value is not None:
            pd_start = pd.Timestamp(start_date.value)
            filtered_df = filtered_df[datetime_col >= pd_start]
            datetime_col = pd.to_datetime(filtered_df["Date"], errors="coerce")

        if end_date.value is not None:
            pd_end = pd.Timestamp(end_date.value)
            filtered_df = filtered_df[datetime_col <= pd_end]

    if search_pay_item.value.strip():
        query_text = search_pay_item.value.strip()
        keywords = query_text.split()
        regex_pattern = "".join([f"(?=.*{re.escape(word)})" for word in keywords])

        match_index = filtered_df.index.str.contains(query_text, case=False, na=False)
        match_desc = filtered_df["Pay Item Description"].str.contains(regex_pattern, case=False, na=False,
                                                                      regex=True)
        filtered_df = filtered_df[match_index | match_desc]

    if search_contract.value:
        filtered_df = filtered_df[filtered_df["Contract"].str.contains(search_contract.value, case=False, na=False)]

    # ---- CORE DATA SORTING ENGINE ----
    is_ascending = chosen_sort_order.value == "Ascending"

    if chosen_sort_col.value == "Pay Item #":
        filtered_df = filtered_df.sort_index(ascending=is_ascending)
    elif chosen_sort_col.value == "Quantity":
        filtered_df = filtered_df.iloc[
            pd.to_numeric(filtered_df["Quantity"], errors="coerce").argsort(kind='mergesort')]
        if not is_ascending:
            filtered_df = filtered_df.iloc[::-1]
    else:
        filtered_df = filtered_df.sort_values(by=chosen_sort_col.value, ascending=is_ascending, na_position='last')

    total_records = len(filtered_df)
    active_filter_count = sum([
        selected_county.value != "All",
        selected_district.value != "Any",
        selected_awarded.value != "Any",
        min_quantity.value is not None,
        max_quantity.value is not None,
        start_date.value is not None,
        end_date.value is not None,
    ])
    filter_summary = "More Filters"
    if active_filter_count:
        filter_summary += f" ({active_filter_count} active)"

    # Keep the search controls within the viewport even when the table is wide.
    solara.Style("""
        .openbidz-app {
            width: 100%;
            max-width: 100%;
            min-width: 0;
            box-sizing: border-box;
            container-type: inline-size;
            container-name: openbidz;
        }
        .openbidz-panel {
            width: 100%;
            min-width: 0;
            max-width: 100%;
            box-sizing: border-box;
        }
        .openbidz-panel .v-card__text {
            min-width: 0;
            max-width: 100%;
            box-sizing: border-box;
        }
        .openbidz-app .openbidz-controls {
            display: grid !important;
            grid-template-columns: minmax(0, 1fr);
            gap: 12px;
            width: 100%;
            min-width: 0;
            max-width: 100%;
            box-sizing: border-box;
        }
        .openbidz-controls > * {
            min-width: 0;
            max-width: 100%;
        }
        @container openbidz (min-width: 900px) {
            .openbidz-app .openbidz-controls {
                grid-template-columns: repeat(2, minmax(0, 1fr));
            }
        }
        @container openbidz (min-width: 1200px) {
            .openbidz-app .openbidz-filter-controls {
                grid-template-columns: repeat(3, minmax(0, 1fr));
            }
        }
        .openbidz-panel .v-expansion-panel-header {
            min-height: 48px;
        }
        .openbidz-panel .v-expansion-panel-content__wrap {
            padding: 0 12px 12px;
        }
        .openbidz-results-scroll {
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
        .openbidz-results-scroll.openbidz-dark-table {
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
            .openbidz-panel {
                padding: 4px !important;
            }
            .openbidz-panel .v-card__title {
                font-size: 1.15rem;
                line-height: 1.4;
                overflow-wrap: anywhere;
            }
            .openbidz-panel .v-card__text {
                padding: 12px;
            }
            .openbidz-panel .v-btn {
                min-height: 44px;
            }
        }
    """)

    with solara.Column(classes=["openbidz-app"], gap="12px"):
        with solara.Row(justify="end", style="padding: 0 12px; min-height: 48px;"):
            solara.Switch(label="Dark Mode", value=dark_mode, on_value=set_dark_mode)

        # ---- COMPACT RESPONSIVE SEARCH PANEL ----
        with solara.Card(
            "Search IDOT Bid Items",
            classes=["openbidz-panel"], margin=0,
            style="margin-bottom: 16px; padding: 12px;",
        ):
            solara.Markdown(
                "Search by pay item, description, or contract number.",
                style="margin-bottom: 8px;",
            )
            with solara.Column(classes=["openbidz-controls"], gap="12px"):
                solara.InputText("Pay Item # or Description", value=search_pay_item)
                solara.InputText("Contract Number", value=search_contract)

            with solara.ColumnsResponsive(default=12, small=12, medium=[8, 4], gutters_dense=True):
                solara.Markdown(
                    f"**{total_records:,}** matching bid item records",
                    style="margin: 8px 0;",
                )
                solara.Button(label="Clear Filters", on_click=clear_filters)

            # Additional filters and sorting start collapsed.
            with solara.Details(summary=filter_summary, expand=False):
                with solara.Column(classes=["openbidz-controls", "openbidz-filter-controls"], gap="12px"):
                    solara.Select(label="County", value=selected_county, values=available_counties)
                    solara.Select(label="District", value=selected_district, values=available_districts)
                    solara.Select(label="Awarded", value=selected_awarded, values=awarded_options)
                    solara.InputFloat(label="Min Quantity", value=min_quantity)
                    solara.InputFloat(label="Max Quantity", value=max_quantity)
                    solara.lab.InputDate(label="Start Letting Date", value=start_date, clearable=True)
                    solara.lab.InputDate(label="End Letting Date", value=end_date, clearable=True)

            with solara.Details(summary="Sort Options", expand=False):
                with solara.Column(classes=["openbidz-controls"], gap="12px"):
                    solara.Select(label="Sort By", value=chosen_sort_col, values=sort_columns)
                    solara.Select(label="Direction", value=chosen_sort_order, values=sort_orders)

        # ---- MAIN DATAFRAME RESULTS PANEL ----
        with solara.Card(
            "IDOT Bid Items Search Results",
            classes=["openbidz-panel"], margin=0,
            style="padding: 12px;",
        ):
            # Prepare a clean display DataFrame (drop the unused "Item" column)
            display_df = filtered_df.drop(columns=["Item"], errors="ignore")

            # ---- DYNAMIC CSV FILE DOWNLOAD COMPONENT ----
            with solara.ColumnsResponsive(default=12, small=12, medium=[8, 4], gutters_dense=True):
                solara.Markdown(f"### Found **{total_records:,}** matching bid item records.")

                if total_records > 0:
                    csv_data = display_df.to_csv(index=True)
                    solara.FileDownload(
                        data=csv_data,
                        filename="filtered_bid_results.csv",
                        label="📥 Download as CSV",
                        mime_type="text/csv"
                    )

            # Render formatted grid or error state
            if total_records > 0:
                start_idx = current_page.value * ITEMS_PER_PAGE
                end_idx = start_idx + ITEMS_PER_PAGE
                sliced_df = display_df.iloc[start_idx:end_idx].copy()

                html_table = render_results_table(sliced_df)
                table_classes = ["openbidz-results-scroll"]
                if dark_mode:
                    table_classes.append("openbidz-dark-table")
                solara.HTML(
                    tag="div",
                    classes=table_classes,
                    unsafe_innerHTML=html_table,
                    attributes={"tabindex": "0", "aria-label": "Scroll bid item results horizontally"},
                )

                # ---- NATIVE PAGINATION INTERFACE BAR ----
                max_pages = (total_records - 1) // ITEMS_PER_PAGE
            
                with solara.Row(justify="center", style="margin-top: 15px; align-items: center; flex-wrap: wrap; gap: 8px;"):
                    solara.Button(
                        label="◀ Previous", 
                        disabled=current_page.value == 0, 
                        on_click=lambda: current_page.set(max(0, current_page.value - 1))
                    )
                    solara.Markdown(f"**Page {current_page.value + 1} of {max_pages + 1}**", style="margin: 0 15px;")
                    solara.Button(
                        label="Next ▶", 
                        disabled=current_page.value >= max_pages, 
                        on_click=lambda: current_page.set(min(max_pages, current_page.value + 1))
                    )
            else:
                # FIXED INDENTATION: Aligned correctly under the main total_records block check
                solara.Error("No records found matching current filtering combinations.")
