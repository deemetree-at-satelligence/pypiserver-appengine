"""This is an example of setting up the Pypiserver extension to work on GCP App Engine.

The small setup: 

1. Pypiserver with this plugin runs on App Engine
2. Package files are stored on the bucket
3. Package files are syncronized between the service's local storage and the bucket
4. Syncrhonization happens on pre- and post- hooks of handling the requests

Code description:

1. The plugin is configured on top of the pypiserver's underlying bottle web framework
2. It checks for consistency between the files stored in local and remote storage (see `StorageClient`)
3. Different `ContentChangeEvent`s represent the change situations
4. Using different `_file_store_driver`s as strategies they are able to synchronize the content.

Environment Configurations:

1. BUCKET_NAME - used to specify the destination GCP Storage bucket
2. LOCAL_PACKAGE_DIRECTORY - used to specify the local directory for packages, 
      make sure it is set to `/tmp` when running on AppEngine
3. REMOTE_PACKAGE_DIRECTORY - used to indicate the directory on a remote bucket (or other destination)
4. TIER - a tier configuration, can be used to enable various default configurations (see `GlobalSettings` class)

Deployment:

NB: Before your deployment create a requirements.txt file with at least `gunicorn, pypiserver, google-cloud-storage`
Examle `requirements.txt`:
      ```plaintext
      pypiserver==1.3.1
      gunicorn==20.0.4
      google-cloud-storage==1.24.1
      ```

1. Create `app.yaml` file for a Python 3.7+ runtime as described in
      https://cloud.google.com/appengine/docs/standard/python3/config/appref, 
      note that you can specify environmental variables in `app.yaml` 
2. Specify the `entrypoint: gunicorn -b :8081 -w 2 'main:get_app()'` attribute in `app.yaml`
      to connect to the wrapped pypiserver app
3. Make sure a bucket with <YOUR-BUCKET-NAME> exists and specify BUCKET_NAME and REMOTE_PACKAGE_DIRECTORY
      according to your setup
4. Deploy the app as described in 
      https://cloud.google.com/appengine/docs/standard/python/getting-started/deploying-the-application,
      i.e. `gcloud app deploy app.yaml`
5. Access your app using 
      `gcloud app browse -s <YOUR-SERVICE-NAME>`
      where <YOUR-SERVICE-NAME> is the name of a service as specified in `app.yaml`

Running locally:

1. Make sure `gunicorn` is installed
2. Run ` gunicorn --reload -b :8081 -w 2 'main:get_app()'`

Dealing with pypiserver's password setup:

1. To add a password specification, simply include a `htpasswd.txt` as described in
      https://github.com/pypiserver/pypiserver#apache-like-authentication-htpasswd
2. Create the `htpasswd.txt` file in the root of the application code (on the same level as `main.py`)
3. Specify the password file when creating a pypiserver app:
      ```python
      pypiserver_app = app(root=[GlobalSettings.LOCAL_DIRECTORY], password_file="htpasswd.txt")
      ```
4. Deploy the app as described in "Deployment" section

P.S: code can be made cleaner.
P.P.S: if you run into troubles, please include your traces and std outputs in comments
"""

import logging
import os
import shutil
import sys
import time

from google.cloud import storage
from pypiserver import app


class GlobalSettings:

    DEV_TIER = "dev"
    TIER = os.getenv("TIER", DEV_TIER)

    RUNNING_DEV = TIER == DEV_TIER

    BUCKET_NAME = os.getenv("BUCKET_NAME", "YOUR-DEFAULT-BUCKET-NAME")

    LOCAL_DIRECTORY = os.getenv(
        "LOCAL_PACKAGE_DIRECTORY", "./packages" if RUNNING_DEV else "/tmp")
    REMOTE_DIRECTORY = os.getenv(
        "REMOTE_PACKAGE_DIRECTORY", "./.remote_packages" if RUNNING_DEV else "packages")


LOGGER = logging.getLogger(__name__)


class SynchronizerPlugin:

    def __init__(self, storage_client=None):
        self.storage_client = storage_client

    def sync_data_before_change(self):
        LOGGER.info("Checking out newest remote state")

        self.storage_client.pull_remote_files()
        self.storage_client.store_local_snapshot()

        LOGGER.debug(self.storage_client.get_local_contents())
        LOGGER.info("Ready to process!")

    def sync_data_after_change(self):
        LOGGER.info("Syncronizing data after request handling")

        change_events = self.storage_client.get_change_events()

        result = [self.storage_client.upload_to_remote(
            change_event) for change_event in change_events]

        LOGGER.debug("Handled events: {}".format(result))
        LOGGER.info("Done!")


class ContentChangeEvent:

    REMOVAL = "removal"
    ADDITION = "addition"
    ANY = "n/a"

    def __init__(self, difference=None):
        self._type = self.ANY
        self._difference = difference

    @property
    def change_type(self):
        return self._type

    @property
    def difference(self):
        return self._difference

    def handle(self, *args):
        raise NotImplementedError("Subclasses must implement a change handler")

    def process(self):
        try:
            results = [self.handle(file_name) for file_name in self.difference]
            LOGGER.debug(
                "Completed handling own type: {} with results: {}".format(self.change_type, results))
            return True
        except Exception as error:
            LOGGER.error(error)
            return False


class RemovalChangeEvent(ContentChangeEvent):

    def __init__(self, file_store_driver, difference=None):
        super().__init__(difference=difference)
        self._type = self.REMOVAL
        self._file_store_driver = file_store_driver

    def handle(self, *args):
        LOGGER.debug(
            "Handling {} with difference: {}".format(self.change_type, self.difference))
        return self._file_store_driver.remove_from_remote(*args)


class AdditionChangeEvent(ContentChangeEvent):

    def __init__(self, file_store_driver, difference=None):
        super().__init__(difference=difference)
        self._type = self.ADDITION
        self._file_store_driver = file_store_driver

    def handle(self, *args):
        LOGGER.debug(
            "Handling {} with difference: {}".format(self.change_type, self.difference))
        return self._file_store_driver.upload_to_remote(*args)


class StorageClient:

    def __init__(self, file_store_driver=None):
        self._file_storage = file_store_driver
        self._current_local_contents = None

    def pull_remote_files(self):
        return self._file_storage.pull_all_remote_files()

    def get_local_contents(self):
        return set(self._file_storage.get_local_file_listing())

    def store_local_snapshot(self):
        self._current_local_contents = self.get_local_contents()

    def get_last_local_snapshot(self):
        if not self._current_local_contents:
            raise ValueError("Local snapshot have not been stored!")
        return self._current_local_contents

    def get_change_events(self):
        last_snapshot = self.get_last_local_snapshot()
        current_contents = self.get_local_contents()
        removal_difference = last_snapshot - current_contents
        addition_difference = current_contents - last_snapshot

        LOGGER.debug("last snapshot: {}".format(last_snapshot))
        LOGGER.debug("current snapshot: {}".format(current_contents))
        LOGGER.debug("removal difference: {}".format(removal_difference))
        LOGGER.debug("addition difference: {}".format(addition_difference))

        yield RemovalChangeEvent(self._file_storage, difference=removal_difference)
        yield AdditionChangeEvent(self._file_storage, difference=addition_difference)

    def upload_to_remote(self, change_event):
        return change_event.process()


class StandardFileStoreManager:

    def __init__(self, local_directory=None, remote_directory=None):
        self._local_directory = local_directory
        self._remote_directory = remote_directory

    @property
    def sync_directory_path(self):
        return self._remote_directory.rstrip("/")

    @property
    def source_directory_path(self):
        return self._local_directory.rstrip("/")

    def pull_all_remote_files(self):
        try:
            remote_files = self.get_remote_file_names()
            results = [self.copy_from_remote(file_name)
                       for file_name in remote_files]
            LOGGER.debug(results)
            return results
        except Exception as error:
            LOGGER.debug(error)
            raise

    def _get_remote_target_path(self, file_name):
        return "{}/{}".format(self.sync_directory_path, file_name)

    def _get_local_target_path(self, file_name):
        return "{}/{}".format(self.source_directory_path, file_name)

    def get_remote_file_names(self):
        raise NotImplementedError(
            "Subclasses must implement `get_remote_file_names`")

    def remove_from_remote(self, file_name):
        raise NotImplementedError(
            "Subclasses must implement `remove_from_remote`")

    def copy_from_remote(self, file_name):
        raise NotImplementedError(
            "Subclasses must implement `copy_from_remote`")

    def upload_to_remote(self, file_name):
        raise NotImplementedError(
            "Subclasses must implement `upload_to_remote`")


class LocalFileStoreManager(StandardFileStoreManager):

    def __init__(self, local_directory=None, remote_directory=None):
        super().__init__(local_directory=local_directory, remote_directory=remote_directory)

    def get_remote_file_names(self):
        file_names = set(os.listdir(self.sync_directory_path))
        LOGGER.debug("FILE NAMES")
        LOGGER.debug(file_names)
        return file_names

    def get_local_file_listing(self):
        return set(os.listdir(self.source_directory_path))

    def remove_from_remote(self, file_name):
        return self._remove_file(trg=self._get_remote_target_path(file_name))

    def copy_from_remote(self, file_name):
        return self._copy_file(src=self._get_remote_target_path(file_name),
                               trg=self._get_local_target_path(file_name))

    def upload_to_remote(self, file_name):
        return self._copy_file(src=self._get_local_target_path(file_name),
                               trg=self._get_remote_target_path(file_name))

    def _copy_file(self, src=None, trg=None):
        LOGGER.debug("{} -> {}".format(src, trg))
        try:
            shutil.copy(src, trg)
            return True
        except:
            return False

    def _remove_file(self, trg=None):
        LOGGER.debug("{} -> x".format(trg))
        try:
            os.remove(trg)
            return True
        except:
            return False


class LocalToGoogleCloudStorageFileStoreManager(StandardFileStoreManager):

    def __init__(self, local_directory=None, remote_directory=None):
        super().__init__(local_directory=local_directory, remote_directory=remote_directory)
        self._bucket_name = GlobalSettings.BUCKET_NAME
        self._google_storage_client = storage.Client()

    @property
    def bucket(self):
        return self._google_storage_client.get_bucket(self._bucket_name)

    def get_remote_file_names(self):
        def get_name(x): return x.name.split('/')[-1]
        def is_file(x): return not x.name.endswith("/")

        blobs = self._google_storage_client.list_blobs(
            self._bucket_name, prefix=self.sync_directory_path)
        file_names = set((get_name(blob) for blob in blobs if is_file(blob)))

        LOGGER.debug(file_names)
        return file_names

    def get_local_file_listing(self):
        return set(os.listdir(self.source_directory_path))

    def remove_from_remote(self, file_name):
        return self._remove_remote_file(trg=self._get_remote_target_path(file_name))

    def copy_from_remote(self, file_name):
        return self._download_file(src=self._get_remote_target_path(file_name),
                                   trg=self._get_local_target_path(file_name))

    def upload_to_remote(self, file_name):
        return self._upload_file(src=self._get_local_target_path(file_name),
                                 trg=self._get_remote_target_path(file_name))

    def _download_file(self, src=None, trg=None):
        LOGGER.debug("r: {} -> l: {}".format(src, trg))
        try:
            blob = self.bucket.blob(src)
            blob.download_to_filename(trg)
            return True
        except:
            return False

    def _upload_file(self, src=None, trg=None):
        LOGGER.debug("l: {} -> r: {}".format(src, trg))
        try:
            blob = self.bucket.blob(trg)
            blob.upload_from_filename(src)
            return True
        except:
            return False

    def _remove_remote_file(self, trg=None):
        LOGGER.debug("{} -> x".format(trg))
        try:
            blob = self.bucket.blob(trg)
            blob.delete()
            return True
        except:
            return False


class AppConfiguration:
    app = app
    plugin = SynchronizerPlugin
    client = StorageClient
    driver = LocalFileStoreManager if GlobalSettings.RUNNING_DEV else LocalToGoogleCloudStorageFileStoreManager

    @classmethod
    def describe_configuration(cls):
        LOGGER.info(
            "Pypiserver served using:\n\tApp:{app}\n\tPlugin:{plugin}\n\tStorageClient:{client}\n\tFileStoreManager:{driver}".format(
                app=cls.app,
                plugin=cls.plugin,
                client=cls.client,
                driver=cls.driver
            )
        )

    @classmethod
    def build_driver(cls):
        return cls.driver(local_directory=GlobalSettings.LOCAL_DIRECTORY, remote_directory=GlobalSettings.REMOTE_DIRECTORY)

    @classmethod
    def build_plugin(cls):
        return cls.plugin(storage_client=cls.client(file_store_driver=cls.build_driver()))


def get_app():
    AppConfiguration.describe_configuration()
    plugin = AppConfiguration.build_plugin()
    pypiserver_app = app(root=[GlobalSettings.LOCAL_DIRECTORY])
    pypiserver_app.add_hook("before_request", plugin.sync_data_before_change)
    pypiserver_app.add_hook("after_request", plugin.sync_data_after_change)
    return pypiserver_app

