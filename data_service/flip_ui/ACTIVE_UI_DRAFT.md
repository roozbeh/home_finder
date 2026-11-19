# Active property UI draft

Local entry: /admin/active. The navigation has an Active properties link. Search filters city, min/max price and minimum bedrooms. Map/list modes share the same 60-result page; pagination retains filters. The map is a Leaflet 1.9.4 preview using OpenStreetMap tiles, with attribution. A tile provider suitable for expected production traffic must be selected before deployment at scale. List access works if the map CDN fails.

/admin/active?candidates=1 shows the ten selected review candidates. Demo viewers are restricted to these ten. The active universe uses stored ACTV, NEW, BOMK and PCH statuses within the nine Bay Area counties and approximate regional coordinate bounds. It is the latest imported MLS snapshot rather than a live MLS availability check.

/admin/active/<MLS_ID> shows photos, property facts, public description, saved photo categories (remaining images stay Unclassified), resale evidence, and a browser-only scenario calculator. No AI API call occurs on page load. active_renovation_insights may provide resale_midpoint, renovation_low, renovation_high and comps; the initial ten have only a preliminary nearby-sale comparison, which is not used automatically as an after-repair estimate. HOA is required input because fee frequency must be verified.

The calculator reports a purchase ceiling and range from low/high repairs, includes acquisition, sale and ten-month carrying costs, and allows a target dollar profit. Inputs are not yet persisted. Missing renovation or resale estimates remain pending; zero repairs are accepted only when explicitly entered. This draft is for local review, with live AI assessment generation, comparable-condition adjustments, scenario persistence and viewport-based map search as subsequent work.

Validation: Python and JavaScript syntax; authenticated active search, candidate shortcut, pagination, city/price filters and detail rendering; anonymous redirect; demo ten-property restriction and outside-property denial; invalid price rejection; scenario arithmetic and invalid renovation-range handling. Browser visual inspection could not run because the session has no enabled CUA browser surface.
