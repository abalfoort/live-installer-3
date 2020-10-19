#!/usr/bin/env python3

# Install python3-geoip, python3-tz

import GeoIP
import pytz
import requests
import re
import os
import subprocess

# List of URLs to get the users IP address
ip_urls = ['https://api.ipify.org/',
           'https://ipinfo.io/ip/',
           'https://ifconfig.me/ip/']
    
# Check IP address validity and return found IP address and IP version
def _get_ip_info(ip_address):
    ip_ver = 0
    try:
        # Test for IP4
        ip_address = re.search(r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}', ip_address).group()
        ip_ver = 4
    except:
        try:
            # Test for IP6
            ip_address = re.search(r'([0-9a-fA-F][0-9a-fA-F]{0,3}:){3,7}:([0-9a-fA-F][0-9a-fA-F]{0,3})', ip_address).group()
            ip_ver = 6
        except:
            ip_ver = 0
    return (ip_address, ip_ver)

def get_geoip_tz(ip_address=None):
    ccode = None
    tz = None
    ip_ver = 0

    # Use IP APIs from configuration file
    if not ip_address:
        for ip_url in ip_urls:
            ip_address, ip_ver = _get_ip_info(requests.get(ip_url).text)
            if ip_address and ip_ver > 0: break
    
    # IP address was passed: check IP validity and get IP version
    if ip_address and ip_ver == 0:
        ip_address, ip_ver = _get_ip_info(ip_address)

    # Get country code and time zone
    try:
        if ip_ver == 6:
            # Need to load the database manually for IP6
            db = '/usr/share/GeoIP/GeoIPv6.dat'
            if not os.path.exists(db):
                command = "dpkg -S GeoIPv6.dat | awk '{print $2}'"
                db = subprocess.check_output(command, shell=True).decode('utf-8').strip().split('\n')[0]
            geoip = GeoIP.open(db, GeoIP.GEOIP_STANDARD)
            ccode = geoip.country_code_by_addr_v6(ip_address)
        else:
            geoip = GeoIP.new(GeoIP.GEOIP_MEMORY_CACHE)
            ccode = geoip.country_code_by_addr(ip_address)
        
        # Loop through the timezones
        tz = pytz.country_timezones(ccode)[0]
                
    except ValueError:
        print(('Warning: address/netmask is invalid: %s' % ip_address))
            
    return (ccode, tz)

