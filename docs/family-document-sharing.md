# Family document sharing

## Goal

Family members can add indexed PDF and Microsoft Office files to a family
album, preview supported files in HomeMind, and download the original without
exposing the server's local filesystem path.

## Scope

### Supported formats

- PDF: `.pdf`
- Word: `.doc`, `.docx`
- Excel: `.xls`, `.xlsx`, `.xlsm`
- PowerPoint: `.ppt`, `.pptx`

### User flow

1. A family member indexes a server directory in **Family assets**.
2. The member opens **Albums & files** and adds an indexed asset to an album.
3. Document entries show the filename and document type, with preview and
   download actions.
4. Modern formats use HomeMind's existing in-app document viewer. Legacy
   formats that cannot be rendered remain downloadable.

### Access and privacy

- Album membership does not bypass family or asset permissions.
- Preview and download use the authenticated family asset content endpoint.
- Local `file://` paths are never shown in the album UI or returned as share
  links.
- Removing an asset from an album removes only the album link, not the source
  file or its asset index.

## Acceptance criteria

- PDF, Word, Excel, and PowerPoint files are classified as `DOCUMENT` assets
  consistently on Linux and Windows.
- Every supported document can be added to and removed from an album.
- PDF, DOCX, XLSX/XLSM, and PPTX open in the existing document preview.
- Legacy DOC, XLS, and PPT show a download fallback when preview is unavailable.
- Download preserves the original filename and bytes.
- A user without access to the family or asset cannot preview or download it.
- The album UI remains responsive and does not display server-local paths.

## Out of scope

- Anonymous or public share links
- Uploading files from the browser
- Editing Office documents in the family album
- Converting legacy Office files on the server

