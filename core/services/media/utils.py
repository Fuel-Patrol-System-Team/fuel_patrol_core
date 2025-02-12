import os


def get_upload_path(filename):
    ext = filename.split('.')[-1]
    if ext in ['csv', 'xlsx']:
        folder = 'datasets'
    return os.path.join(folder, filename)