#!/usr/bin/env python3

# Install python3-geoip, python3-tz
# https://github.com/maxmind/geoip-api-python
# https://pypi.org/project/pytz/

import GeoIP
import pytz
import requests
import re
import os
import subprocess
from datetime import datetime 

# List of URLs to get the users IP address
ip_urls = ['https://api.ipify.org/',
           'https://ipinfo.io/ip/',
           'https://ifconfig.me/ip/']

# List with all time zones
all_timezones = pytz.all_timezones

# Dictionary[ccode] = country_name
country_names = pytz.country_names
    
# Check IP address validity and return found IP address and IP version
def _get_ip_info(ip_str):
    ip_ver = 0
    try:
        # Test for IP4
        ip = re.search(r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}', ip_str).group()
        ip_ver = 4
    except:
        try:
            # Test for IP6
            ip = re.search(r'([0-9a-fA-F][0-9a-fA-F]{0,3}:){3,7}:([0-9a-fA-F][0-9a-fA-F]{0,3})', ip_str).group()
            ip_ver = 6
        except:
            ip_ver = 0
    return (ip, ip_ver)

def get_timezones_date_time(timezone, human_readable=True, utc=True):
    if not timezone:
        return None
    tz = pytz.timezone(timezone)
    if utc:
        dt = datetime.utcnow().astimezone(tz=tz)
    else:
        dt = datetime.now(tz=tz)
    if human_readable:
        return dt.strftime('%Y-%m-%d %H:%M:%S %Z%z')
    else:
        return dt

def get_timezones(ccode):
    if not ccode:
        return None
    return pytz.country_timezones(ccode)

def country_code_by_addr(ip=None):
    ccode = None
    ip_ver = 0

    # Use IP APIs from configuration file
    if not ip:
        for ip_url in ip_urls:
            # timeout separate for connect and read
            try:
                ip, ip_ver = _get_ip_info(requests.get(ip_url, verify=False, timeout=(3, 2)).text)
                #import urllib2
                #req = urllib2.Request(ip_url)
                #ip, ip_ver = _get_ip_info(urllib2.urlopen(req, timeout=3).read())
            except:
                pass
            if ip and ip_ver > 0: break
    
    # IP address was passed: check IP validity and get IP version
    if ip and ip_ver == 0:
        ip, ip_ver = _get_ip_info(ip)

    # Get country code and time zone
    try:
        if ip_ver == 6:
            # Need to load the database manually for IP6
            db = '/usr/share/GeoIP/GeoIPv6.dat'
            if not os.path.exists(db):
                command = "dpkg -S GeoIPv6.dat | awk '{print $2}'"
                db = subprocess.check_output(command, shell=True).decode('utf-8').strip().split('\n')[0]
            geoip = GeoIP.open(db, GeoIP.GEOIP_STANDARD)
            ccode = geoip.country_code_by_addr_v6(ip)
        else:
            geoip = GeoIP.new(GeoIP.GEOIP_MEMORY_CACHE)
            ccode = geoip.country_code_by_addr(ip)
                
    except ValueError:
        print(('Warning: address/netmask is invalid: "{ip}"'.format(ip)))
            
    return ccode
