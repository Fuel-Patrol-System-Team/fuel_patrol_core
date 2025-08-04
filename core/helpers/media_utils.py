import hashlib
import os


def get_upload_path(filename):
    ext = filename.split('.')[-1]
    if ext in ['csv', 'xlsx']:
        folder = 'datasets'
    return os.path.join(folder, filename)


def calculate_file_hash(file_obj):
    hash_sha256 = hashlib.sha256()
    for chunk in file_obj.chunks():
        hash_sha256.update(chunk)
    return hash_sha256.hexdigest()