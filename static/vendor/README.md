# Vendored libraries

Copied from npm so the site works with no internet connection. Licences are beside each.

| Folder | Package | Version | Licence |
|---|---|---|---|
| `highlight/` | `@highlightjs/cdn-assets` (`highlight.min.js`, the github and github-dark styles) | 11.12.0 | BSD-3-Clause |
| `katex/` | `katex` (`katex.min.js`, `katex.min.css`, the woff2 fonts) | 0.19.0 | MIT |
| `mermaid/` | `mermaid` (`mermaid.min.js`, loaded only when a reply has a diagram) | 12.1.0 | MIT |

To update: `npm pack <package>@<version>`, unpack, and copy the same files over.
