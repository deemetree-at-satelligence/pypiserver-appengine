# pypiserver-appengine

Running a pypiserver on AppEngine backend with Google Cloud Storage.

> Disclaimer: This is not a library but an example of a way how to set it up.

## The small setup

1. Pypiserver with this plugin runs on App Engine
2. Package files are stored on the bucket
3. Package files are syncronized between the service's local storage and the bucket
4. Syncrhonization happens on pre- and post- hooks of handling the requests

## Code description

1. The plugin is configured on top of the pypiserver's underlying bottle web framework
2. It checks for consistency between the files stored in local and remote storage (see `StorageClient`)
3. Different `ContentChangeEvent`s represent the change situations
4. Using different `_file_store_driver`s as strategies they are able to synchronize the content.

## Environment Configurations

1. BUCKET_NAME - used to specify the destination GCP Storage bucket
2. LOCAL_PACKAGE_DIRECTORY - used to specify the local directory for packages, 
      make sure it is set to `/tmp` when running on AppEngine
3. REMOTE_PACKAGE_DIRECTORY - used to indicate the directory on a remote bucket (or other destination)
4. TIER - a tier configuration, can be used to enable various default configurations (see `GlobalSettings` class)

### NB

Before running create a requirements.txt file with at least `gunicorn`, `pypiserver`, `google-cloud-storage`
Examle `requirements.txt`:

        ```plaintext
        pypiserver==1.3.1
        gunicorn==20.0.4
        google-cloud-storage==1.24.1
        ```

## Deployment

1. Create `app.yaml` file for a Python 3.7+ runtime as described in
      https://cloud.google.com/appengine/docs/standard/python3/config/appref, 
      note that you can specify environmental variables in `app.yaml` 
2. Specify the `entrypoint: gunicorn -b :8081 -w 2 'main:get_app()'` attribute in `app.yaml`
      to connect to the wrapped pypiserver app
3. Make sure a bucket with \<YOUR-BUCKET-NAME\> exists and specify BUCKET_NAME and REMOTE_PACKAGE_DIRECTORY
      according to your setup
4. Deploy the app as described in
      https://cloud.google.com/appengine/docs/standard/python/getting-started/deploying-the-application,
      i.e. `gcloud app deploy app.yaml`
5. Access your app using
      `gcloud app browse -s <YOUR-SERVICE-NAME>`
      where \<YOUR-SERVICE-NAME\> is the name of a service as specified in `app.yaml`

## Running locally

1. Make sure `gunicorn` is installed
2. Run `gunicorn --reload -b :8081 -w 2 'main:get_app()'`

Dealing with pypiserver's password setup:

1. To add a password specification, simply include a `htpasswd.txt` as described in
      https://github.com/pypiserver/pypiserver#apache-like-authentication-htpasswd
2. Create the `htpasswd.txt` file in the root of the application code (on the same level as `main.py`)
3. Specify the password file when creating a pypiserver app:

        ```python
        ...
        pypiserver_app = app(root=[GlobalSettings.LOCAL_DIRECTORY], password_file="htpasswd.txt")
        ....
        ```

4. Deploy the app as described in "Deployment" section

## Remarks

This effort is also available as a gist:
https://gist.github.com/deemetree-at-satelligence/793ca96bd571bba40ce1f35e9951e73b.  
And is an open issue in `pypiserver` to include the functionality in the core of the package:
https://github.com/pypiserver/pypiserver/issues/322.

## Post Scriptum

P.S: code can be made cleaner.  
P.P.S: if you run into troubles, please include your traces and std outputs in comments  
P.P.P.S: this readme could be made better  
P.P.P.P.S: this is not a library