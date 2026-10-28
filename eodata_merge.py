from pathlib import Path

import xarray as xr
from cartopy import crs as ccrs
from cartopy import feature as cfeature
from matplotlib import pyplot as plt
from tqdm import tqdm

from eodata import sum_ds

full = None
count = 0
for path in tqdm(sorted(Path(".").glob("LCFM*/*.nc"))):
    ds = xr.load_dataset(path)

    if full is None:
        full = ds
    else:
        full = sum_ds(full, ds)
    count += 1

assert full is not None

print(full)
print(f"Used {count} tiles")
full.to_netcdf("land-cover_cdse_10km.nc")

fig, axs = plt.subplots(
    nrows=3,
    ncols=1,
    figsize=(10, 6),
    subplot_kw={"projection": ccrs.PlateCarree()},
    sharex=True,
    sharey=True,
)

for ax, name in zip(axs, ["Tree", "Cropland", "Built-up"]):
    full[name].plot(ax=ax, vmin=0, vmax=1, cmap="Reds", add_colorbar=False)
    ax.add_feature(cfeature.COASTLINE, color="purple", lw=0.1)
    ax.set(xticks=range(-160, 181, 20), ylabel=name)
fig.tight_layout()
fig.savefig("/tmp/sdasd.pdf", dpi=300)
