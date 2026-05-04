/* CSB v1 vs v2 — MapLibre + PMTiles compare viewer.
   Both archives served from Source Cooperative; no build step. */

const SOURCES = {
  usda: {
    url: "pmtiles://https://data.source.coop/ftw/usda-csb/csb_2025.pmtiles",
    label: "USDA v1",
    color: "#e89c2b",
    sourceLayer: "fields", // USDA's archive uses 'fields' not 'csb'
  },
  ours: {
    url: "pmtiles://https://data.source.coop/ftw/usda-csb/csb_v2_2025.pmtiles",
    label: "Ours v2",
    color: "#3aa8ff",
    sourceLayer: "csb", // tippecanoe -l csb
  },
};

const protocol = new pmtiles.Protocol();
maplibregl.addProtocol("pmtiles", protocol.tile);

const map = new maplibregl.Map({
  container: "map",
  style: {
    version: 8,
    glyphs: "https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf",
    sources: {
      basemap: {
        type: "raster",
        tiles: [
          "https://cartodb-basemaps-a.global.ssl.fastly.net/dark_nolabels/{z}/{x}/{y}.png",
          "https://cartodb-basemaps-b.global.ssl.fastly.net/dark_nolabels/{z}/{x}/{y}.png",
          "https://cartodb-basemaps-c.global.ssl.fastly.net/dark_nolabels/{z}/{x}/{y}.png",
          "https://cartodb-basemaps-d.global.ssl.fastly.net/dark_nolabels/{z}/{x}/{y}.png",
        ],
        tileSize: 256,
        attribution:
          '<a href="https://carto.com/attributions">CARTO</a> · <a href="https://openstreetmap.org/copyright">OSM</a>',
      },
      basemap_labels: {
        type: "raster",
        tiles: [
          "https://cartodb-basemaps-a.global.ssl.fastly.net/dark_only_labels/{z}/{x}/{y}.png",
          "https://cartodb-basemaps-b.global.ssl.fastly.net/dark_only_labels/{z}/{x}/{y}.png",
          "https://cartodb-basemaps-c.global.ssl.fastly.net/dark_only_labels/{z}/{x}/{y}.png",
          "https://cartodb-basemaps-d.global.ssl.fastly.net/dark_only_labels/{z}/{x}/{y}.png",
        ],
        tileSize: 256,
      },
      "csb-usda": {
        type: "vector",
        url: SOURCES.usda.url,
        attribution: "USDA NASS · Hunt et al. 2024",
      },
      "csb-ours": {
        type: "vector",
        url: SOURCES.ours.url,
        attribution: "Corley 2026 — github.com/isaaccorley/crop-sequence-boundaries-v2",
      },
    },
    layers: [
      { id: "bg", type: "background", paint: { "background-color": "#0a0d12" } },
      { id: "basemap", type: "raster", source: "basemap" },
      {
        id: "csb-usda-fill",
        type: "fill",
        source: "csb-usda",
        "source-layer": SOURCES.usda.sourceLayer,
        paint: {
          "fill-color": SOURCES.usda.color,
          "fill-opacity": 0.55,
          "fill-antialias": true,
        },
      },
      {
        id: "csb-ours-fill",
        type: "fill",
        source: "csb-ours",
        "source-layer": SOURCES.ours.sourceLayer,
        paint: {
          "fill-color": SOURCES.ours.color,
          "fill-opacity": 0.55,
          "fill-antialias": true,
        },
      },
      {
        id: "csb-usda-line",
        type: "line",
        source: "csb-usda",
        "source-layer": SOURCES.usda.sourceLayer,
        minzoom: 9,
        paint: {
          "line-color": SOURCES.usda.color,
          "line-width": 0.5,
          "line-opacity": 0.85,
        },
      },
      {
        id: "csb-ours-line",
        type: "line",
        source: "csb-ours",
        "source-layer": SOURCES.ours.sourceLayer,
        minzoom: 9,
        paint: {
          "line-color": SOURCES.ours.color,
          "line-width": 0.5,
          "line-opacity": 0.85,
        },
      },
      { id: "labels", type: "raster", source: "basemap_labels" },
    ],
  },
  center: [-95.5, 39.5],
  zoom: 4.0,
  hash: true,
  attributionControl: { compact: true },
});

map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), "top-right");
map.addControl(new maplibregl.ScaleControl({ unit: "metric", maxWidth: 120 }), "bottom-right");

// Layer-toggle buttons
const usdaLayers = ["csb-usda-fill", "csb-usda-line"];
const oursLayers = ["csb-ours-fill", "csb-ours-line"];
const groupLayers = { usda: usdaLayers, ours: oursLayers };

function setLayerVisible(group, visible) {
  for (const id of groupLayers[group]) {
    if (!map.getLayer(id)) continue;
    map.setLayoutProperty(id, "visibility", visible ? "visible" : "none");
  }
  document
    .querySelector(`.layer-toggle[data-layer="${group}"]`)
    .setAttribute("aria-pressed", String(visible));
}

document.querySelectorAll(".layer-toggle").forEach((btn) => {
  btn.addEventListener("click", () => {
    const group = btn.dataset.layer;
    const next = btn.getAttribute("aria-pressed") === "false";
    setLayerVisible(group, next);
  });
});

// Opacity sliders
function setOpacity(group, pct) {
  const fillId = `csb-${group}-fill`;
  const lineId = `csb-${group}-line`;
  if (map.getLayer(fillId)) map.setPaintProperty(fillId, "fill-opacity", pct / 100);
  if (map.getLayer(lineId)) map.setPaintProperty(lineId, "line-opacity", Math.min(1, pct / 100 + 0.3));
}
document.getElementById("usda-opacity").addEventListener("input", (e) => setOpacity("usda", e.target.value));
document.getElementById("ours-opacity").addEventListener("input", (e) => setOpacity("ours", e.target.value));

// Quick mode buttons
document.querySelectorAll(".quick-modes button").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".quick-modes button").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    const mode = btn.dataset.mode;
    setLayerVisible("usda", mode === "both" || mode === "usda");
    setLayerVisible("ours", mode === "both" || mode === "ours");
  });
});

// Clickable polygons → popup with CDL columns
function popupHandler(group) {
  return (e) => {
    if (!e.features || !e.features.length) return;
    const f = e.features[0];
    const props = f.properties || {};

    // v2 schema: CDL2018..CDL2025 columns. v1 schema: CSBYEARS string.
    const cdlYears = Object.keys(props)
      .filter((k) => /^CDL\d{4}$/.test(k))
      .sort();
    const rows = cdlYears.length
      ? cdlYears
          .map((k) => `<tr><td>${k.slice(3)}</td><td>${props[k]}</td></tr>`)
          .join("")
      : props.CSBYEARS
        ? String(props.CSBYEARS)
            .split(/[,\s]+/)
            .filter(Boolean)
            .map((v, i) => `<tr><td>yr${i + 1}</td><td>${v}</td></tr>`)
            .join("")
        : "";

    const acres = props.CSBACRES || props.Shape_area;
    const html = `
      <div class="popup">
        <div class="popup-tag" style="background:${SOURCES[group].color}">${SOURCES[group].label}</div>
        <div class="popup-id">CSBID ${props.CSBID ?? "—"}</div>
        ${acres ? `<div class="popup-meta">${Number(acres).toFixed(1)} ${props.CSBACRES ? "ac" : "m²"}</div>` : ""}
        ${rows ? `<table class="popup-cdl"><thead><tr><th>year</th><th>CDL</th></tr></thead><tbody>${rows}</tbody></table>` : ""}
      </div>
    `;
    new maplibregl.Popup({ offset: 8, closeButton: true, maxWidth: "260px" })
      .setLngLat(e.lngLat)
      .setHTML(html)
      .addTo(map);
  };
}
map.on("click", "csb-usda-fill", popupHandler("usda"));
map.on("click", "csb-ours-fill", popupHandler("ours"));
for (const id of [...usdaLayers, ...oursLayers]) {
  map.on("mouseenter", id, () => (map.getCanvas().style.cursor = "pointer"));
  map.on("mouseleave", id, () => (map.getCanvas().style.cursor = ""));
}

// Inject popup CSS once
const popupCss = document.createElement("style");
popupCss.textContent = `
  .maplibregl-popup-content {
    background: #11161e !important;
    color: #e9eef7 !important;
    border: 1px solid #232c3a !important;
    border-radius: 8px !important;
    font-family: "Inter", system-ui, sans-serif !important;
    padding: 12px 14px !important;
  }
  .maplibregl-popup-tip { display: none !important; }
  .maplibregl-popup-close-button { color: #8893a8 !important; right: 6px !important; top: 4px !important; }
  .popup-tag {
    display: inline-block; padding: 2px 8px; border-radius: 999px;
    font-size: 10.5px; font-weight: 600; color: #0a0d12;
    letter-spacing: 0.04em; text-transform: uppercase; margin-bottom: 6px;
  }
  .popup-id { font-size: 13px; font-weight: 600; }
  .popup-meta { font-size: 11.5px; color: #8893a8; margin-bottom: 8px; }
  .popup-cdl { width: 100%; border-collapse: collapse; font-size: 11.5px; }
  .popup-cdl th { color: #8893a8; font-weight: 500; text-align: left; padding: 2px 0; border-bottom: 1px solid #232c3a; }
  .popup-cdl td { padding: 2px 0; }
  .popup-cdl td:last-child { text-align: right; font-family: "JetBrains Mono", ui-monospace, monospace; }
`;
document.head.appendChild(popupCss);

map.on("error", (e) => {
  if (e?.error?.message?.includes("source-layer")) {
    console.warn(
      "Source-layer mismatch — v1 expects 'fields', v2 expects 'csb'. " +
        "Inspect the archive metadata to confirm."
    );
  }
});
