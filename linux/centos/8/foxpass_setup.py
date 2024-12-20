#!/usr/bin/python3

# Copyright (c) 2016-present, Foxpass, Inc.
# All rights reserved.
#
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# 1. Redistributions of source code must retain the above copyright notice, this
#    list of conditions and the following disclaimer.
# 2. Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions and the following disclaimer in the documentation
#    and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
# ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR
# ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
# (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
# ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
# SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

import argparse
from datetime import datetime
import difflib
import os
import re
import sys
import time
import urllib3


def main():
    parser = argparse.ArgumentParser(description='Set up Foxpass on a linux host.')
    parser.add_argument('--base-dn', required=True, help='Base DN')
    parser.add_argument('--bind-user', required=True, help='Bind User')
    parser.add_argument('--bind-pw', required=True, help='Bind Password')
    parser.add_argument('--api-key', required=True, help='API Key')
    parser.add_argument('--ldap-uri', '--ldap', default='ldaps://ldap.foxpass.com', help='LDAP Server')
    parser.add_argument('--secondary-ldap', dest='ldaps', default=[], action='append', help='Secondary LDAP Server(s)')
    parser.add_argument('--api-url', '--api', default='https://api.foxpass.com', help='API Url')
    parser.add_argument('--secondary-api', dest='apis', default=[], action='append', help='Secondary API Server(s)')
    parser.add_argument('--sudoers-group', default='foxpass-sudo', type=str, help='sudoers group with root access')
    parser.add_argument('--update-sudoers', default=False, action='store_true', help='update 95-foxpass-sudo with new group')
    parser.add_argument('--require-sudoers-pw', default=False, action='store_true', help='set sudoers default password requirement')
    parser.add_argument('--opt-timeout', default=6, help='option to set the sssd opt timeout')
    parser.add_argument('--debug', default=False, action='store_true', help='Turn on debug mode')
    # Foxpass SUDOers add-on
    parser.add_argument('--enable-ldap-sudoers', default=False, action='store_true', help='Enable Foxpass SUDOers')
    parser.add_argument('--sudo-timed', default=False, action='store_true', help='Toggle sudo_timed parameter')
    parser.add_argument('--full-refresh-interval', default=21600, help='Default is 6 hours in seconds')
    parser.add_argument('--smart-refresh-interval', default=900, help='Default is 15 minutes in seconds')

    args = parser.parse_args()

    bind_dn = 'cn=%s,%s' % (args.bind_user, args.base_dn)
    apis = [args.api_url] + args.apis

    if args.debug:
        foxpass_ssh_keys_path = '/usr/local/sbin/foxpass_ssh_keys.sh'
        sssd_path = '/etc/sssd/conf.d/authconfig-sssd.conf'
        sshd_config_path = '/etc/ssh/sshd_config'
        ldap_path = '/etc/openldap/ldap.conf'
        nsswitch_path = '/etc/nsswitch.conf'
        sudoers_path = '/etc/sudoers'
        foxpass_sudo_path = '/etc/sudoers.d/95-foxpass-sudo'

        from_file_foxpass_ssh_keys = open_file(foxpass_ssh_keys_path)
        from_file_sssd = open_file(sssd_path)
        from_file_sshd_config = open_file(sshd_config_path)
        from_file_ldap = open_file(ldap_path)
        from_file_nsswitch = open_file(nsswitch_path)
        from_sudoers_file = open_file(sudoers_path)
        from_foxpass_sudo_file = open_file(foxpass_sudo_path)

    install_dependencies()
    write_foxpass_ssh_keys_script(apis, args.api_key)
    run_authconfig(args.ldap_uri, args.base_dn)
    configure_sssd(bind_dn, args.bind_pw, args.ldaps, args.opt_timeout)
    augment_sshd_config()
    fix_sudo(args.sudoers_group, args.require_sudoers_pw, args.update_sudoers)

    if args.enable_ldap_sudoers:
        configure_ldap_sudoers(args.base_dn, args.sudo_timed, args.full_refresh_interval, args.smart_refresh_interval)

    if args.debug:
        to_file_foxpass_ssh_keys = open_file(foxpass_ssh_keys_path)
        to_file_sssd = open_file(sssd_path)
        to_file_sshd_config = open_file(sshd_config_path)
        to_file_ldap = open_file(ldap_path)
        to_file_nsswitch = open_file(nsswitch_path)
        to_sudoers_file = open_file(sudoers_path)
        to_foxpass_sudo_file = open_file(foxpass_sudo_path)

        diff_files(from_file_foxpass_ssh_keys, to_file_foxpass_ssh_keys, foxpass_ssh_keys_path)
        diff_files(from_file_sssd, to_file_sssd, sssd_path)
        diff_files(from_file_sshd_config, to_file_sshd_config, sshd_config_path)
        diff_files(from_file_ldap, to_file_ldap, ldap_path)
        diff_files(from_file_nsswitch, to_file_nsswitch, nsswitch_path)
        diff_files(from_sudoers_file, to_sudoers_file, sudoers_path)
        diff_files(from_foxpass_sudo_file, to_foxpass_sudo_file, foxpass_sudo_path)

    # sleep to the next second to make sure sssd.conf has a new timestamp
    time.sleep(1)
    # touch the sssd conf file again
    os.system('touch /etc/sssd/conf.d/authconfig-sssd.conf')
    os.system('chmod 600 /etc/sssd/conf.d/authconfig-sssd.conf')

    restart()


def install_dependencies():
    # install dependencies
    os.system('yum install -y sssd authconfig')


def write_foxpass_ssh_keys_script(apis, api_key):
    base_curl = 'curl -q --disable --silent --fail --max-time 5 --header "Authorization: Token ${secret}" "%s/sshkeys/?user=${user}&hostname=${hostname}'
    curls = []
    for api in apis:
        curls.append(base_curl % api)

    with open('/usr/local/sbin/foxpass_ssh_keys.sh', "w") as w:
        if is_ec2_host():
            append = '&aws_instance_id=${aws_instance_id}&aws_region_id=${aws_region_id}" 2>/dev/null'
            curls = [curl + append for curl in curls]
            contents = r"""#!/bin/bash

user="$1"
secret="%s"
pwfile="/etc/passwd"
hostname=$(hostname)
if grep -q "^${user/./\\.}:" $pwfile; then echo "User $user found in file $pwfile, exiting." > /dev/stderr; exit; fi
common_curl_args="--disable --silent --fail"
aws_token=$(curl $common_curl_args --max-time 10 --request PUT --header "X-aws-ec2-metadata-token-ttl-seconds: 30" "http://169.254.169.254/latest/api/token")
if [ -z "$aws_token" ]
then
  aws_instance_id=$(curl $common_curl_args "http://169.254.169.254/latest/meta-data/instance-id")
  aws_region_id=$(curl $common_curl_args "http://169.254.169.254/latest/meta-data/placement/region")
else
  aws_instance_id=$(curl $common_curl_args --header "X-aws-ec2-metadata-token: ${aws_token}" "http://169.254.169.254/latest/meta-data/instance-id")
  aws_region_id=$(curl $common_curl_args --header "X-aws-ec2-metadata-token: ${aws_token}" "http://169.254.169.254/latest/meta-data/placement/region")
fi
%s
exit $?
"""
        elif is_gce_host():
            append = '&provider=gce&gce_instance_id=${gce_instance_id}&gce_zone=${gce_zone}&gce_project_id=${gce_project_id}${gce_networks}${gce_network_tags}" 2>/dev/null'
            curls = [curl + append for curl in curls]
            contents = r"""#!/bin/bash

user="$1"
secret="%s"
pwfile="/etc/passwd"
hostname=$(hostname)
headers="Metadata-Flavor: Google"
if grep -q "^${user/./\\.}:" $pwfile; then echo "User $user found in file $pwfile, exiting." > /dev/stderr; exit; fi
common_curl_args="--disable --silent --fail"
gce_instance_id=`curl $common_curl_args -H "${headers}" http://metadata.google.internal/computeMetadata/v1/instance/id`
gce_zone=`curl $common_curl_args -H "${headers}" http://metadata.google.internal/computeMetadata/v1/instance/zone`
gce_project_id=`curl $common_curl_args -H "${headers}" http://metadata.google.internal/computeMetadata/v1/project/project-id`
networks=(`curl $common_curl_args -H "${headers}" http://metadata.google.internal/computeMetadata/v1/instance/network-interfaces/`)
gce_networks=''
for gce_network in "${networks[@]}"
do
    gce_network=`curl $common_curl_args -H "${headers}" http://metadata.google.internal/computeMetadata/v1/instance/network-interfaces/${gce_network}network`
    gce_networks="${gce_networks}&gce_networks[]=${gce_network}"
done
network_tags=(`curl $common_curl_args -H "${headers}" http://metadata.google.internal/computeMetadata/v1/instance/tags?alt=text`)
gce_network_tags=''
for gce_network_tag in "${network_tags[@]}"
do
    gce_network_tags="${gce_network_tags}&gce_network_tags[]=${gce_network_tag}"
done
%s
exit $?
"""
        else:
            append = '" 2>/dev/null'
            curls = [curl + append for curl in curls]
            contents = r"""#!/bin/bash

user="$1"
secret="%s"
pwfile="/etc/passwd"
hostname=$(hostname)
if grep -q "^${user/./\\.}:" $pwfile; then echo "User $user found in file $pwfile, exiting." > /dev/stderr; exit; fi
%s
exit $?
"""
        w.write(contents % (api_key, ' || '.join(curls)))

        # give permissions only to root to protect the API key inside
        os.system('chmod 700 /usr/local/sbin/foxpass_ssh_keys.sh')
    

def run_authconfig(uri, base_dn):
    cmd = 'authconfig --enablesssd --enablesssdauth --enablelocauthorize --enableldap --enableldapauth --ldapserver={uri} --disableldaptls --ldapbasedn={base_dn} --enablemkhomedir --enablecachecreds --update'.format(uri=uri, base_dn=base_dn)
    print('Running %s' % cmd)
    os.system(cmd)


def configure_sssd(bind_dn, bind_pw, backup_ldaps, opt_timeout):
    from SSSDConfig import SSSDConfig

    sssdconfig = SSSDConfig()
    sssdconfig.import_config('/etc/sssd/conf.d/authconfig-sssd.conf')

    sssdconfig.new_service('pam')
    sssdconfig.new_service('nss')
    sssdconfig.activate_service('pam')
    sssdconfig.activate_service('nss')
    domain = sssdconfig.get_domain('default')
    domain.add_provider('ldap', 'id')
    if backup_ldaps:
        domain.set_option('ldap_backup_uri', ','.join(backup_ldaps))
    domain.set_option('ldap_tls_reqcert', 'demand')
    domain.set_option('ldap_tls_cacert', '/etc/ssl/certs/ca-bundle.crt')
    domain.set_option('ldap_default_bind_dn', bind_dn)
    domain.set_option('ldap_default_authtok', bind_pw)
    domain.set_option('ldap_opt_timeout', opt_timeout)
    domain.set_option('enumerate', True)
    domain.remove_option('ldap_tls_cacertdir')

    domain.set_active(True)

    sssdconfig.save_domain(domain)
    sssdconfig.write()


def configure_ldap_sudoers(base_dn, sudo_timed, full_refresh_interval, smart_refresh_interval):
    from SSSDConfig import SSSDConfig
    sssdconfig = SSSDConfig()
    sssdconfig.import_config('/etc/sssd/conf.d/authconfig-sssd.conf')

    try:
        sssdconfig.new_service('sudo')
        sssdconfig.activate_service('sudo')
    except:
        pass

    domain = sssdconfig.get_domain('default')
    domain.add_provider('ldap', 'sudo')
    domain.set_option('ldap_sudo_search_base', 'ou=SUDOers,{}'.format(base_dn))
    domain.set_option('ldap_sudo_full_refresh_interval', full_refresh_interval)
    domain.set_option('ldap_sudo_smart_refresh_interval', smart_refresh_interval)
    domain.set_option('ldap_sudo_include_regexp', 'True')
    domain.set_option('ldap_sudo_include_regexp', 'True')
    sssdconfig.activate_service('sudo')
    sssdconfig.set('sudo', 'sudo_timed', str(sudo_timed).lower())
    sssdconfig.save_domain(domain)
    sssdconfig.write()

    augment_openldap(base_dn)
    augment_nsswitch()


def augment_sshd_config():
    if not file_contains('/etc/ssh/sshd_config', r'^AuthorizedKeysCommand\w'):
        with open('/etc/ssh/sshd_config', "a") as w:
            w.write("\n")
            w.write("AuthorizedKeysCommand\t\t/usr/local/sbin/foxpass_ssh_keys.sh\n")
            w.write("AuthorizedKeysCommandUser\troot\n")


def augment_openldap(bind_dn):
    if not file_contains('/etc/openldap/ldap.conf', r'^SUDOERS_BASE'):
        with open('/etc/openldap/ldap.conf', "a") as w:
            w.write("\nSUDOERS_BASE ou=SUDOers,{}".format(bind_dn))


def augment_nsswitch():
    if not file_contains('/etc/nsswitch.conf', r'^sudoers:'):
        with open('/etc/nsswitch.conf', "a") as w:
            w.write("sudoers: files sss")


# give "wheel" and chosen sudoers groups sudo permissions without password
def fix_sudo(sudoers, require_sudoers_pw, update_sudoers):
    if not file_contains('/etc/sudoers', r'^#includedir /etc/sudoers.d'):
        with open('/etc/sudoers', 'a') as w:
            w.write('\n#includedir /etc/sudoers.d\n')
    if not os.path.exists('/etc/sudoers.d'):
        os.system('mkdir /etc/sudoers.d && chmod 750 /etc/sudoers.d')
    if not os.path.exists('/etc/sudoers.d/95-foxpass-sudo') or update_sudoers:
        with open('/etc/sudoers.d/95-foxpass-sudo', 'w') as w:
            w.write('# Adding Foxpass group to sudoers\n%{sudo} ALL=(ALL:ALL) {command}'.
                    format(sudo=sudoers, command='ALL' if require_sudoers_pw else 'NOPASSWD:ALL'))
    if not require_sudoers_pw:
        os.system("sed -i 's/^# %wheel\tALL=(ALL)\tNOPASSWD: ALL/%wheel\tALL=(ALL)\tNOPASSWD:ALL/' /etc/sudoers")


def restart():
    os.system("service sssd restart")
    os.system("service sshd restart")


def file_contains(filename, pattern):
    with open(filename) as f:
        for line in f:
            if re.search(pattern, line):
                return True
    return False


def is_gce_host():
    http = urllib3.PoolManager(timeout=.1)
    url = 'http://metadata.google.internal/computeMetadata/v1/instance/'
    try:
        r = http.request('GET', url, headers={"Metadata-Flavor": "Google"})
        if r.status != 200:
            raise Exception
        return True
    except Exception:
            return False


def is_ec2_host():
    http = urllib3.PoolManager(timeout=.1)
    url = 'http://169.254.169.254/latest/api/token'
    try:
        r = http.request('PUT', url, headers={"X-aws-ec2-metadata-token-ttl-seconds": 30})
        if r.status != 200:
            raise Exception
        return True
    except Exception:
        return is_ec2_host_imds_v1_fallback()


def is_ec2_host_imds_v1_fallback():
    http = urllib3.PoolManager(timeout=.1)
    url = 'http://169.254.169.254/latest/meta-data/instance-id'
    try:
        r = http.request('GET', url)
        pattern="^i-[a-f0-9]{8}(?:[a-f0-9]{9})?$"
        # Check the response if it is returning the right instance id.
        # The medatada endpoint works on VMWare vm but it's not the value we are expecting.
        if re.match(pattern, r.data.decode('utf-8')):
            return True
        else:
            raise Exception
    except Exception:
        return False


def open_file(path):
    if os.path.exists(path):
        with open(path, 'r') as file:
            return file.readlines()
    else:
        return []


def diff_files(from_file, to_file, filename):
    diff = difflib.unified_diff(from_file, to_file, fromfile='Old {}'.format(filename), tofile='New {}'.format(filename))
    for line in diff:
        sys.stdout.write(line)


if __name__ == '__main__':
    main()
