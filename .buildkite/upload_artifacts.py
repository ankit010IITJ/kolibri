"""
# Requirements:
    * Generate access token in your Github account, then create environment variable GITHUB_ACCESS_TOKEN.
        - e.g export GITHUB_ACCESS_TOKEN=1ns3rt-my-t0k3n-h3re.

    * Generate a service account key for your Google API credentials, then create environment variable GOOGLE_APPLICATION_CREDENTIALS.
        - e.g export GOOGLE_APPLICATION_CREDENTIALS=/path/to/credentials.json.

# Environment Variable/s:
    * IS_KOLIBRI_RELEASE = Upload artifacts to the Google Cloud as a release candidate.
    * GITHUB_ACCESS_TOKEN = Personal access token used to authenticate in your Github account via API.
    * BUILDKITE_BUILD_NUMBER = Build identifier for each directory created.
    * BUILDKITE_PULL_REQUEST = Pull request issue or the value is false.
    * BUILDKITE_TAG = Tag identifier if this build was built from a tag.
    * BUILDKITE_COMMIT = Git commit hash that the build was made from.
    * GOOGLE_APPLICATION_CREDENTIALS = Your service account key.
"""
import json
import logging
import os
import sys
from os import listdir

import requests
from gcloud import storage

logging.getLogger().setLevel(logging.INFO)

ACCESS_TOKEN = os.getenv("GITHUB_ACCESS_TOKEN")
REPO_OWNER = "learningequality"
REPO_NAME = "kolibri"
ISSUE_ID = os.getenv("BUILDKITE_PULL_REQUEST")
BUILD_ID = os.getenv("BUILDKITE_BUILD_NUMBER")
TAG = os.getenv("BUILDKITE_TAG")
COMMIT = os.getenv("BUILDKITE_COMMIT")


RELEASE_DIR = 'release'
PROJECT_PATH = os.path.join(os.getcwd())

# Python packages artifact location
DIST_DIR = os.path.join(PROJECT_PATH, "dist")

# Installer artifact location
INSTALLER_DIR = os.path.join(PROJECT_PATH, "installer")

headers = {'Authorization': 'token %s' % ACCESS_TOKEN}

# Manifest of files, keyed by extension
file_manifest = {
    'exe': {
        'extension': 'exe',
        'description': 'Windows Installer',
        'category': 'installer',
        'content_type': 'application/x-ms-dos-executable',
    },
    'pex': {
        'extension': 'pex',
        'description': 'Pex file',
        'category': 'Python package',
        'content_type': 'application/octet-stream',
    },
    'whl': {
        'extension': 'whl',
        'description': 'Whl file',
        'category': 'Python package',
        'content_type': 'application/zip',
    },
    'zip': {
        'extension': 'zip',
        'description': 'Zip file',
        'category': 'Python package',
        'content_type': 'application/zip',
    },
    'gz': {
        'extension': 'gz',
        'description': 'Tar file',
        'category': 'Python package',
        'content_type': 'application/gzip',
    },
    'apk': {
        'extension': 'apk',
        'description': 'Android Installer',
        'category': 'installer',
        'content_type': 'application/vnd.android.package-archive',
    },
}

file_order = [
    'exe',
    'apk',
    'pex',
    'whl',
    'zip',
    'gz',
]

session = requests.Session()

def create_status_report_html(artifacts):
    html = "<html>\n<body>\n<h1>Build Artifacts</h1>\n"
    current_heading = None
    for ext in file_order:
        artifact = artifacts[ext]
        if artifact['category'] != current_heading:
            current_heading = artifact['category']
            html += "<h2>{heading}</h2>\n".format(heading=current_heading)
        html += "<p>{description}: <a href='{media_url}'>{name}</a></p>\n".format(
            **artifact
        )
    html += "</body>\n</html>"
    return html

def create_github_status(report_url):
    url = 'https://api.github.com/repos/{owner}/{repo}/statuses/{commit}'.format(
        owner=REPO_OWNER,
        repo=REPO_NAME,
        commit=COMMIT
    )
    payload = {
        "state": "success",
        "target_url": report_url,
        "description": "Kolibri Buildkite assets",
        "context": "buildkite/kolibri/assets"
    }
    r = session.post(url, data=payload, headers=headers)
    if r.status_code == 201:
        logging.info('Successfully created Github status(%s).' % url)
    else:
        logging.info('Error encounter(%s). Now exiting!' % r.status_code)
        sys.exit(1)


def collect_local_artifacts():
    """
    Create a dict of the artifact name and the location.
    """

    artifacts_dict = {}

    def create_artifact_data(artifact_dir):
        for artifact in listdir(artifact_dir):
            filename, file_extension = os.path.splitext(artifact)
            # Remove leading '.'
            file_extension = file_extension[1:]
            if file_extension in file_manifest:
                data = {"name": artifact,
                        "file_location": "%s/%s" % (artifact_dir, artifact)}
                data.update(file_manifest[file_extension])
                logging.info("Collect file data: (%s)" % data)
                artifacts_dict[file_extension] = data
    create_artifact_data(DIST_DIR)
    create_artifact_data(INSTALLER_DIR)
    return artifacts_dict


def upload_artifacts():
    """
    Upload the artifacts on the Google Cloud Storage.
    Create a comment on the pull requester with artifact media link.
    """
    client = storage.Client()
    bucket = client.bucket("le-downloads")
    artifacts = collect_local_artifacts()
    is_release = os.getenv("IS_KOLIBRI_RELEASE")
    for file_data in artifacts.values():
        logging.info("Uploading file (%s)" % (file_data.get("name")))
        if is_release:
            blob = bucket.blob('kolibri/%s/%s/%s' % (RELEASE_DIR, BUILD_ID, file_data.get("name")))
        else:
            blob = bucket.blob('kolibri/buildkite/build-%s/%s/%s' % (ISSUE_ID, BUILD_ID, file_data.get("name")))
        blob.upload_from_filename(filename=file_data.get("file_location"))
        blob.make_public()
        file_data.update({'media_url': blob.media_link})

    html = create_status_report_html(artifacts)

    blob = bucket.blob('kolibri/%s/%s/report.html' % (RELEASE_DIR, BUILD_ID))

    blob.upload_from_string(html, content_type='text/html')

    create_github_status(blob.media_link)

    if TAG:
        # Building from a tag, this is probably a release!
        get_release_asset_url = requests.get("https://api.github.com/repos/{owner}/{repo}/releases/tags/{tag}".format(
            owner=REPO_OWNER,
            repo=REPO_NAME,
            tag=TAG,
        ))
        if get_release_asset_url.status_code == 200:
            # Definitely a release!
            release_id = json.loads(get_release_asset_url.content)['id']
            url = "https://api.github.com/repos/{owner}/{repo}/releases/{id}/assets".format(
                owner=REPO_OWNER,
                repo=REPO_NAME,
                id=release_id,
            )
            for file_extension in file_order:
                artifact = artifacts[file_extension]
                params = {
                    'name': artifact['name'],
                    'label': artifact['description']
                }
                files = {
                    'file': (artifact['name'], open(artifact['file_location'], 'rb'), artifact['content_type'])
                }
                session.post(url, params=params, files=files, headers=headers)


def main():
    upload_artifacts()


if __name__ == "__main__":
    main()
