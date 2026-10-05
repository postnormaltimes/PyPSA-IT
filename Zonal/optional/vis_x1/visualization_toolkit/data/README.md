# Bundled plotting geography

`naturalearth_lowres.geojson` contains the 177-country Natural Earth low
resolution dataset, converted locally from the installed pyogrio test
fixture `runtime/venv/Lib/site-packages/pyogrio/tests/fixtures/naturalearth_lowres/naturalearth_lowres.shp` to EPSG:4326 GeoJSON. Natural Earth data are public domain. This context layer covers the MEM markets and neighboring coastlines/borders, but it is coarse and must not be used as a market-zone polygon or transmission constraint. The original R2B Italy figure used a separate 2021 NUTS2 proxy layer that was not recovered as data.

Source dataset: Natural Earth Admin 0 low-resolution country polygons, supplied as the `pyogrio` installed test fixture. The fixture does not declare a Natural Earth release version, so that version remains unknown. Transformation: read the installed Shapefile and serialize all 177 country features to GeoJSON in geographic EPSG:4326 (GeoJSON CRS84 longitude/latitude). No model boundaries or market labels were inserted. The packaged file's SHA-256 is `c83795a6615b793401ac04d642792d9a2fb11d3a8392fd9908b81805d7e9a114`. The package resolves it relative to `visualization_toolkit`, regardless of current working directory. A caller can instead provide a CRS-declared geography path or GeoDataFrame.
