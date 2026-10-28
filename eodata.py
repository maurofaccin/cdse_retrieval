import json
import os
import sys
import time
from itertools import product
from pathlib import Path

import boto3
import numpy as np
import requests
import rioxarray
import xarray as xr
from tqdm import tqdm

import eodata_credentials as credentials

# Set up command line argument parser


# Configuration parameters
CONFIG = {
    "auth_server_url": "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token",
    "odata_base_url": "https://catalogue.dataspace.copernicus.eu/odata/v1/Products",
    "s3_endpoint_url": "https://eodata.dataspace.copernicus.eu",
    "s3_credentials": "https://s3-keys-manager.cloudferro.com/api/user/credentials",
}

COVER_TYPE = {
    "Tree": 10,
    "Shrubland": 20,
    "Grassland": 30,
    "Cropland": 40,
    "Wetland": 50,
    "Mangroves": 60,
    "Moss": 70,
    "Sparse": 80,
    "Built-up": 90,
    "Water": 100,
    "Snow": 110,
}

S3_CREDS = {}


def get_access_token(username, password):
    """
    Retrieve an access token from the authentication server.
    This token is used for subsequent API calls.
    """
    auth_data = {
        "client_id": "cdse-public",
        "grant_type": "password",
        "username": username,
        "password": password,
    }
    response = requests.post(
        CONFIG["auth_server_url"], data=auth_data, verify=True, allow_redirects=False
    )
    if response.status_code == 200:
        return json.loads(response.text)["access_token"]
    else:
        print(f"Failed to retrieve access token. Status code: {response.status_code}")
        sys.exit(1)


def get_eo_product_details(headers, eo_product_name):
    """
    Retrieve EO product details using the OData API to determine the S3 path.
    """
    odata_url = f"{CONFIG['odata_base_url']}?$filter=Name eq '{eo_product_name}'"
    response = requests.get(odata_url, headers=headers)
    if response.status_code == 200:
        eo_product_data = response.json()["value"][0]
        return eo_product_data["Id"], eo_product_data["S3Path"]
    else:
        print(f"Failed to retrieve EO product details. Status code: {response.status_code}")
        sys.exit(1)


def get_temporary_s3_credentials(headers):
    """
    Create temporary S3 credentials by calling the S3 keys manager API.
    """
    if len(S3_CREDS) > 0:
        return
    print(CONFIG["s3_credentials"])
    print(CONFIG["headers"])
    credentials_response = requests.post(CONFIG["s3_credentials"], headers=CONFIG["headers"])
    if credentials_response.status_code == 200:
        s3_credentials = credentials_response.json()
        print("Temporary S3 credentials created successfully.")
        print(f"access: {s3_credentials['access_id']}")
        print(f"secret: {s3_credentials['secret']}")
        return s3_credentials
    else:
        print(
            f"Failed to create temporary S3 credentials. Status code: {credentials_response.status_code}"
        )
        print("Product download aborted.")
        sys.exit(1)


def delete_temporary_s3_credentials(headers):
    delete_response = requests.delete(
        f"https://s3-keys-manager.cloudferro.com/api/user/credentials/access_id/{S3_CREDS['access_id']}",
        headers=headers,
    )
    if delete_response.status_code == 204:
        print("Temporary S3 credentials deleted successfully.")
    else:
        print(
            f"Failed to delete temporary S3 credentials. Status code: {delete_response.status_code}"
        )


def format_filename(filename, length=40):
    """
    Format a filename to a fixed length, truncating if necessary.
    """
    if len(filename) > length:
        return filename[: length - 3] + "..."
    else:
        return filename.ljust(length)


def download_file_s3(s3, bucket_name, s3_key, local_path, failed_downloads):
    """
    Download a file from S3 with a progress bar.
    Track failed downloads in a list.
    """
    try:
        file_size = s3.head_object(Bucket=bucket_name, Key=s3_key)["ContentLength"]
        formatted_filename = format_filename(os.path.basename(local_path))
        with tqdm(
            total=file_size,
            unit="B",
            unit_scale=True,
            desc=formatted_filename,
            ncols=80,
            bar_format="{desc:.40}|{bar:20}| {percentage:3.0f}% {n_fmt}/{total_fmt}B",
        ) as pbar:

            def progress_callback(bytes_transferred):
                pbar.update(bytes_transferred)

            s3.download_file(bucket_name, s3_key, local_path, Callback=progress_callback)
    except Exception as e:
        print(f"Failed to download {s3_key}. Error: {e}")
        failed_downloads.append(s3_key)


def traverse_and_download_s3(
    s3_resource, bucket_name: str, base_s3_path, local_path: Path, failed_downloads
):
    """
    Traverse the S3 bucket and download all files under the specified prefix.
    """
    bucket = s3_resource.Bucket(bucket_name)
    files = bucket.objects.filter(Prefix=base_s3_path)

    for obj in files:
        s3_key = obj.key
        relative_path = os.path.relpath(s3_key, base_s3_path)
        local_path_file = os.path.join(local_path, relative_path)
        local_dir = os.path.dirname(local_path_file)
        os.makedirs(local_dir, exist_ok=True)
        download_file_s3(
            s3_resource.meta.client, bucket_name, s3_key, local_path_file, failed_downloads
        )


def download_product(product_name: str):
    if Path(product_name).is_dir():
        print(product_name, "already downloaded")
        return

    # Step 3: Get EO product details (including S3 path)
    eo_product_id, s3_path = get_eo_product_details(CONFIG["headers"], product_name)
    bucket_name, base_s3_path = s3_path.lstrip("/").split("/", 1)
    print(eo_product_id)

    # Step 5: Set up S3 client and resource with temporary credentials
    time.sleep(5)  # Ensure the key pair is installed
    s3_resource = boto3.resource(
        "s3",
        endpoint_url=CONFIG["s3_endpoint_url"],
        aws_access_key_id=S3_CREDS["access_id"],
        aws_secret_access_key=S3_CREDS["secret"],
    )

    # Step 6: Create the top-level folder and start download
    top_level_folder = Path(product_name)
    top_level_folder.mkdir(parents=True, exist_ok=True)
    failed_downloads = []
    traverse_and_download_s3(
        s3_resource, bucket_name, base_s3_path, top_level_folder, failed_downloads
    )

    # Step 7: Print final status
    if not failed_downloads:
        print("Product download complete.")
    else:
        print("Product download incomplete:")
        for failed_file in failed_downloads:
            print(f"- {failed_file}")


def load_product(product_path: Path):
    ds = rioxarray.open_rasterio(product_path)
    # using the median here introduce a slight bias
    # while preventing rounding errors in the following
    return (
        xr.Dataset(
            {
                name: xr.DataArray(ds == value)
                .astype(np.uint8)
                .coarsen(x=1000, y=1000, boundary="exact", coord_func=np.median)
                .mean()
                for name, value in COVER_TYPE.items()
            }
        )
        .sel(band=1)
        .drop_vars(["band", "spatial_ref"])
    )


def sum_da(ar1: xr.DataArray, ar2: xr.DataArray) -> xr.DataArray:
    return (
        xr.merge([ar1.rename("uno"), ar2.rename("due")], join="outer")
        .to_dataarray()
        .sum("variable", skipna=True)
    )


def sum_ds(ar1: xr.Dataset, ar2: xr.Dataset) -> xr.Dataset:
    keys = ar1.keys() & ar2.keys()
    return xr.Dataset({k: sum_da(ar1[k], ar2[k]) for k in keys})


def main(product_name):
    try:
        download_product(product_name)
    except IndexError:
        Path(product_name).mkdir()

    for filepath in Path(product_name).glob("*.tif"):
        print(filepath)

        ds_coarse = load_product(filepath)
        print("    Writing to", filepath.with_suffix(".nc"))
        ds_coarse.to_netcdf(filepath.with_suffix(".nc"))

        print("    Deleting", filepath)
        filepath.unlink()


if __name__ == "__main__":
    # Retrieve the access token
    access_token = get_access_token(credentials.user, credentials.password)
    # Set up headers for API calls
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
    CONFIG["headers"] = headers
    S3_CREDS = get_temporary_s3_credentials(headers)
    for ns, _ns, ew, _ew in product(["N", "S"], range(0, 91, 3), ["E", "W"], range(0, 181, 3)):
        name = f"LCFM_LCM-10_V100_2020_{ns}{_ns:02d}{ew}{_ew:03d}_cog"
        print(name)
        main(name)
    delete_temporary_s3_credentials(headers)
