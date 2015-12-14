#!/usr/bin/env python3

import hashlib
import os
import subprocess
import tempfile

import apt
import psycopg2
from apt import apt_pkg

SIZE_LIMIT = 500 * 1024


def fetch(source_name, source_version, destdir):
    src = apt_pkg.SourceRecords()
    acq = apt_pkg.Acquire(apt.progress.text.AcquireProgress())

    dsc = None
    source_lookup = src.lookup(source_name)

    # lifted directly from Package.fetch_source()
    while source_lookup and source_version != src.version:
        source_lookup = src.lookup(source_name)
    if not source_lookup:
        raise ValueError("No source for %s %s" % (source_name, source_version))
    files = list()
    for md5, size, path, type_ in src.files:
        base = os.path.basename(path)
        destfile = os.path.join(destdir, base)
        if type_ == 'dsc':
            dsc = destfile
        files.append(apt_pkg.AcquireFile(acq, src.index.archive_uri(path),
                                         md5, size, base, destfile=destfile))
    acq.run()

    for item in acq.items:
        if item.status != item.STAT_DONE:
            raise ValueError("The item %s could not be fetched: %s" %
                             (item.destfile, item.error_text))

    outdir = os.path.join(destdir, 'pkg')
    subprocess.check_call(["dpkg-source", "-x", dsc, outdir])
    return destdir


# lifted directly from apt.cache.Cache():
def root_dir(rootdir):
    rootdir = os.path.abspath(rootdir)
    if os.path.exists(rootdir + "/etc/apt/apt.conf"):
        apt_pkg.read_config_file(apt_pkg.config,
                                 rootdir + "/etc/apt/apt.conf")
    if os.path.isdir(rootdir + "/etc/apt/apt.conf.d"):
        apt_pkg.read_config_dir(apt_pkg.config,
                                rootdir + "/etc/apt/apt.conf.d")
    apt_pkg.config.set("Dir", rootdir)
    apt_pkg.config.set("Dir::State::status",
                       rootdir + "/var/lib/dpkg/status")
    apt_pkg.config.set("Dir::bin::dpkg",
                       os.path.join(rootdir, "usr", "bin", "dpkg"))
    apt_pkg.init_system()


def ingest(fh, cur):
    return write_blob(cur, fh.read())


def write_blob(cur, blob):
    hasher = hashlib.sha1()
    hasher.update('blob {}\0'.format(len(blob)).encode('utf-8'))
    hasher.update(blob)
    hashed = hasher.hexdigest()
    try:
        blob = blob.decode('utf-8')
    except:
        pass

    # there's a race-condition here, but the unique index
    # will just crash us if we mess up anyway.
    cur.execute('insert into blobs (hash, content) select %s, %s'
                + ' where not exists (select 1 from blobs where hash=%s)',
                (hashed, blob, hashed))

    return hashed


def eat(source_package, source_version):
    # root_dir('/home/faux/.local/share/lxc/sid/rootfs')

    with tempfile.TemporaryDirectory() as destdir, \
            psycopg2.connect('dbname=deb2pg') as conn, \
            conn.cursor() as cur:

        try:
            cur.execute('insert into packages(name, version, arch, size_limit) values (%s, %s, %s, %s) returning id',
                        (source_package, source_version, 'amd64', SIZE_LIMIT))
        except psycopg2.IntegrityError:
            # print(source_package, source_version, 'already exists, ignoring')
            return

        pkg_id = cur.fetchone()

        fetch(source_package, source_version, destdir)
        pkgfolder = os.path.join(destdir, 'pkg')
        for dirpath, _, filelist in os.walk(pkgfolder):
            for f in filelist:
                full_name = os.path.join(dirpath, f)
                stat = os.lstat(full_name)
                size = stat.st_size
                symlink = bool(stat.st_mode & 0o020000)
                user_exec = bool(stat.st_mode & 0o100)

                rel_path = os.path.join(os.path.relpath(dirpath, pkgfolder), f)
                if rel_path[0:2] == './':
                    rel_path = rel_path[2:]

                if symlink:
                    mode = '120000'
                elif user_exec:
                    mode = '100755'
                else:
                    mode = '100644'

                hashed = None
                if size < SIZE_LIMIT:
                    if not symlink:
                        with open(full_name, 'rb') as fh:
                            hashed = ingest(fh, cur)
                    else:
                        hashed = write_blob(cur, os.readlink(full_name).encode('utf-8'))

                try:
                    cur.execute('insert into files (package, mode, path, hash) values (%s, %s, %s, %s)',
                                (pkg_id, mode, rel_path.encode('utf-8', 'backslashreplace'), hashed))
                except:
                    print('error processing ', source_package, source_version, hashed,
                          rel_path.encode('utf-8', 'backslashreplace'))
                    raise


def main(specs):
    for spec in specs:
        try:
            eat(*spec.split('=', 1))
        except Exception as e:
            import traceback
            print(spec, ' failed to ingest', traceback.format_exc())


if __name__ == '__main__':
    import sys

    main(sys.argv[1:])
