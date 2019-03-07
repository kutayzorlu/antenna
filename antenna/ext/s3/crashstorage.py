# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import logging

from everett.component import ConfigOptions
from everett.manager import parse_class

from antenna.heartbeat import register_for_verification
from antenna.ext.crashstorage_base import CrashStorageBase
from antenna.util import get_date_from_crash_id, json_ordered_dumps


logger = logging.getLogger(__name__)


class S3CrashStorage(CrashStorageBase):
    """Save raw crash files to S3.

    This will save raw crash files to S3 in a pseudo-tree something like this:

    ::

        <BUCKET>
           v1/
               dump_names/
                   <CRASHID>
               <DUMPNAME>/
                   <CRASHID>
           v2/
               raw_crash/
                   <ENTROPY>/
                       <YYYYMMDD>/
                           <CRASHID>

    """

    required_config = ConfigOptions()
    required_config.add_option(
        'connection_class',
        default='antenna.ext.s3.connection.S3Connection',
        parser=parse_class,
        doc='S3 connection class to use'
    )

    def __init__(self, config):
        self.config = config.with_options(self)
        self.conn = self.config('connection_class')(config)
        register_for_verification(self.verify_bucket_exists)

    def verify_bucket_exists(self):
        """Verify S3 bucket exists.

        Raises an exception if it doesn't.

        """
        self.conn.verify_bucket_exists()

    def get_runtime_config(self, namespace=None):
        """Return generator for items in runtime configuration."""
        for item in super().get_runtime_config(namespace):
            yield item

        for item in self.conn.get_runtime_config(namespace):
            yield item

    def check_health(self, state):
        """Check connection health."""
        self.conn.check_health(state)

    def _get_raw_crash_path(self, crash_id):
        return 'v2/raw_crash/{entropy}/{date}/{crash_id}'.format(
            entropy=crash_id[:3],
            date=get_date_from_crash_id(crash_id),
            crash_id=crash_id
        )

    def _get_dump_names_path(self, crash_id):
        return 'v1/dump_names/{crash_id}'.format(crash_id=crash_id)

    def _get_dump_name_path(self, crash_id, dump_name):
        # NOTE(willkg): This is something that Socorro collector did. I'm not
        # really sure why, but in order to maintain backwards compatability, we
        # need to keep doing it.
        if dump_name in (None, '', 'upload_file_minidump'):
            dump_name = 'dump'

        return 'v1/{dump_name}/{crash_id}'.format(
            dump_name=dump_name,
            crash_id=crash_id
        )

    def save_raw_crash(self, crash_id, raw_crash):
        """Save the raw crash and related dumps.

        .. Note::

           If you're saving the raw crash and dumps, make sure to save the raw
           crash last.

        :arg str crash_id: The crash id as a string.
        :arg dict raw_crash: dict The raw crash as a dict.

        :raises botocore.exceptions.ClientError: connection issues, permissions
            issues, bucket is missing, etc.

        """
        # FIXME(willkg): self.conn.save_file raises a
        # botocore.exceptions.ClientError if the perms aren't right. That needs
        # to surface to "this node is not healthy".

        # Save raw_crash
        self.conn.save_file(
            self._get_raw_crash_path(crash_id),
            json_ordered_dumps(raw_crash).encode('utf-8')
        )

    def save_dumps(self, crash_id, dumps):
        """Save dump data.

        :arg str crash_id: The crash id
        :arg dict dumps: dump name -> dump

        :raises botocore.exceptions.ClientError: connection issues, permissions
            issues, bucket is missing, etc.

        """
        # Save dump_names even if there are no dumps
        self.conn.save_file(
            self._get_dump_names_path(crash_id),
            json_ordered_dumps(list(sorted(dumps.keys()))).encode('utf-8')
        )

        # Save dumps
        for dump_name, dump in dumps.items():
            self.conn.save_file(
                self._get_dump_name_path(crash_id, dump_name),
                dump
            )

    def save_crash(self, crash_report):
        """Save crash data."""
        crash_id = crash_report.crash_id
        raw_crash = crash_report.raw_crash
        dumps = crash_report.dumps

        # Save dumps first
        self.save_dumps(crash_id, dumps)

        # Save raw crash
        self.save_raw_crash(crash_id, raw_crash)
