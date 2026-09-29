# Business filing uploads

Drop an Ohio Secretary of State new business filings report here (.xlsx, .xls, .csv or .txt).

1. Go to ohiosos.gov > Businesses > Business Reports and download a new business filings report.
2. On GitHub, open this folder, click "Add file" > "Upload files", and upload the report.

Uploading starts the update workflow. It keeps only the Grandview Heights filings (business name,
filing type, date and business address) in docs/business-filings.json, then deletes the statewide
report from this folder so it isn't kept in the repo. If a report can't be read, it stays here and
the workflow log says why.
