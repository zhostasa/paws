#
# paws -- provision automated windows and services
# Copyright (C) 2016 Red Hat, Inc.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#

"""Winsetup task."""

from logging import getLogger
from os.path import join, exists, isfile

from paws.constants import ADMIN, WIN_EXEC_YAML, LINE
from paws.exceptions import SSHError
from paws.remote import WinPsExecYAML
from paws.remote.driver import Ansible
from paws.remote.results import GenModuleResults
from paws.tasks import Tasks
from paws.util import cleanup, get_ssh_conn, file_mgmt
from paws.util.decorators import handle_pre_tasks


LOG = getLogger(__name__)


class Winsetup(Tasks):
    """Winsetup.

    The main Winsetup class. This class will configure Windows resources by
    running Windows PowerShell scripts against them using Ansible.
    """

    def __init__(self, args):
        """Constructor."""
        super(Winsetup, self).__init__(args)
        self.ansible = Ansible(args.userdir)
        self.winsetup_yaml = join(args.userdir, WIN_EXEC_YAML)
        self.pshell = join(self.userdir, args.powershell)
        try:
            self.psv = args.powershell_vars
        except AttributeError:
            self.psv = None
        self.resources = []

    @handle_pre_tasks
    def pre_tasks(self):
        """Perform any necessary pre task actions."""
        # Clean files generated by paws
        cleanup([self.winsetup_yaml])

        # Use paws generated topology file?
        if exists(self.resources_paws):
            self.topology_file = self.resources_paws

        # Create inventory file
        self.ansible.create_hostfile(tp_file=self.topology_file)

        # Verify PowerShell script exists
        if not exists(self.pshell):
            LOG.error("PowerShell script: %s does not exist!", self.pshell)
            raise SystemExit(1)

    def get_systems(self):
        """Return a list of systems to configure.

        Paws users can specificy system(s) from CLI to configure. By default
        if none are given, it will configure all resources inside topology.
        """
        _active = []

        res = file_mgmt('r', self.topology_file)

        try:
            for sut in res['resources']:
                if sut['name'] in self.args.systems:
                    self.resources.append(sut)
                _active.append(sut['name'])
        except AttributeError:
            self.resources = res['resources']

        if self.resources.__len__() == 0:
            LOG.error("Systems given do not map to any active resources.")
            LOG.error("Given resources  : %s" % self.resources)
            LOG.error("Active resources : %s" % _active)
            raise SystemExit(1)

    def run(self):
        """The main method for winsetup. This method will create a list of
        systems to configure based on input, verify SSH connections with
        remote systems and then execute PowerShell script against them.
        """
        LOG.info("START: Winsetup")

        # Save start time
        self.rtime.start()

        # Get systems
        self.get_systems()

        # Run PowerShell script against supplied machines
        for res in self.resources:
            pb_vars = {}

            # Get resource IP
            try:
                sut_ip = res['public_v4']
            except KeyError:
                sut_ip = res['ip']

            # Get resource authentication
            try:
                # Authenticate with SSH private key
                sut_ssh_key = res['ssh_key_file']
                sut_ssh_user = ADMIN
                sut_ssh_password = None
            except KeyError:
                # Authenticate with username and password
                sut_ssh_key = None
                sut_ssh_user = res['win_username']
                sut_ssh_password = res['win_password']

            # Initialize playbook variables
            pb_vars["hosts"] = sut_ip
            pb_vars["ps"] = self.pshell

            try:
                _psvfile = join(self.userdir, self.psv)
                if isfile(_psvfile):
                    # PowerShell vars is a file
                    pb_vars["psv"] = _psvfile
                    pvars = "file"
                elif isinstance(self.psv, (str, unicode)):
                    # PowerShell vars is a string
                    pvars = "str"
                    pb_vars["psv"] = self.psv
                else:
                    # PowerShell is neither file or unicode
                    pvars = None
            except (AttributeError, TypeError):
                # No PowerShell vars defined, use default
                pvars = self.psv

            # Create playbook to run PowerShell script on Windows resources
            self.winsetup_yaml = WinPsExecYAML(self).create(pvars)

            # Test if remote machine is ready for SSH connection
            try:
                LOG.info("Attempting to establish SSH connection to %s",
                         sut_ip)
                LOG.info(LINE)
                LOG.info("This could take several minutes to complete.")
                LOG.info(LINE)

                get_ssh_conn(
                    sut_ip,
                    sut_ssh_user,
                    sut_ssh_password,
                    sut_ssh_key
                )
            except SSHError:
                LOG.error("Unable to establish SSH connection to %s", sut_ip)
                raise SystemExit(1)

            # Playbook call - run PowerShell script on Windows resources
            self.ansible.run_playbook(
                self.winsetup_yaml,
                pb_vars,
                results_class=GenModuleResults
            )

        # Perform post tasks
        self.post_tasks()

    def post_tasks(self):
        """Perform any necessary post task actions."""
        # Save end time
        self.rtime.end()

        # Clean up run time files
        if not self.args.verbose:
            cleanup([self.winsetup_yaml], self.userdir)

        # Calculate elapsed time
        hours, minutes, seconds = self.rtime.delta()
        LOG.info("END: Winsetup, TIME: %dh:%dm:%ds", hours, minutes, seconds)