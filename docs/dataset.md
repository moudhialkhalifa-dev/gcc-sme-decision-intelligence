# Dataset provenance

| | |
|---|---|
| Name | Online Retail II |
| Creator | Daqing Chen |
| Publisher | UCI Machine Learning Repository, https://archive.ics.uci.edu/dataset/502/online+retail+ii |
| DOI | 10.24432/C5CG6D |
| Licence | Creative Commons Attribution 4.0 International (CC BY 4.0): sharing and adaptation allowed with credit |
| Content | All transactions of a UK-based, registered, non-store online retailer of gift-ware; many customers are wholesalers |
| Period | 1 December 2009 to 9 December 2011 (the last day is incomplete) |
| Rows | 1,067,371 line items (525,461 in sheet "Year 2009-2010", 541,910 in "Year 2010-2011") |
| Currency | Pounds sterling (GBP). Kept as GBP everywhere; never relabelled or converted |

## How the copy in this repository was obtained

The UCI website could not be reached from the build environment, so the original workbook
`online_retail_II.xlsx` was taken from a public GitHub mirror
(`TarikKaanKoc/Online-Retail-CRM-Analytics`, commit `935bd64a`, file
`dataset/online_retail_II 01.27.45.xlsx`, SHA-256
`bcbe73b35f5b7babf197fb0cb983a11f5d9ff929078d4aa53d171b1f2df2e980`).
Its two sheets contain exactly the 1,067,371 rows that UCI documents and the same eight columns.
`scripts/convert_raw_xlsx.py` writes both sheets, unchanged, to
`data/raw/online_retail_II.csv`; the repository ships that file gzip-compressed.
To rebuild from the official source, download the workbook from UCI and run the same script.

## Fields

| Column | Meaning (UCI description) | Canonical field |
|---|---|---|
| Invoice | 6-digit invoice number; a leading "C" marks a cancellation | transaction_id |
| StockCode | Product code | product_id |
| Description | Product name | product_name |
| Quantity | Units on the line; negative for cancellations | quantity |
| InvoiceDate | Date and time of the invoice | timestamp |
| Price | Unit price in sterling | unit_price |
| Customer ID | 5-digit customer number; missing on 22.8% of rows | customer_id |
| Country | Country where the customer resides | location |

There is no product category, sales channel, cost, inventory or supplier field, so the matching
modules are shown as unavailable for this dataset.

## Limitations

- One UK retailer, 2009 to 2011. It is a development case study and not evidence about GCC companies.
- "Location" is the customer's country, not a branch. 85% of revenue is from the United Kingdom.
- Many customers are wholesalers, so a few very large orders dominate some days.
- Customer IDs are pseudonymous numbers. The API never returns them.
- Postage, fees and manual adjustment lines are identified with a pattern written for this dataset
  (`configs/online_retail_ii.json`). It was chosen by inspecting the non-numeric stock codes.

## Synthetic data

`backend/tests/conftest.py` generates a small synthetic shop (closed on Fridays, two branches,
amounts labelled BHD) for automated tests only. It is never used as evidence in the report.
