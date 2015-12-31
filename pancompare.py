#!/usr/bin/env python3

# noinspection PyPackageRequirements

import re

import netaddr
import pan.xapi
import yaml


class Config:
    def __init__(self, filename):
        with open(filename, 'r') as stream:
            config = yaml.load(stream)
        self.top_domain = config['top_domain']
        self.firewall_api_key = config['firewall_api_key']
        self.firewall_hostnames = config['firewall_hostnames']


def retrieve_dataplane(hostname, api_key, debug=None):
    """
    This takes the FQDN of the firewall and retrieves the dataplane information.
    :param hostname: Hostname (FQDN) of firewall to retrieve information from
    :param api_key:  API key to access firewall configuration
    :param debug: True/False value to determine if debugging mode.
    :return: Dictionary containing dataplane or test unit if Debug is True
    """
    if debug is None:
        firewall = pan.xapi.PanXapi(hostname=hostname, api_key=api_key)
        command = "show running security-policy"
        firewall.op(cmd=command, cmd_xml=True)
        return firewall.xml_result()
    else:
        with open("dataplane_result_test.txt", "r") as test_file:
            test_result = test_file.read()
        return test_result


def convert_to_ipobject(string):
    ip_range_regex = re.compile('([0-9]{1,3}(?:\.[0-9]{1,3}){0,3})-([0-9]{1,3}(?:\.[0-9]{1,3}){0,3})')
    ip_address_regex = re.compile('([0-9]{1,3}(?:\.[0-9]{1,3}){0,3}(?:\/[0-9]+)*)')
    ipset = netaddr.IPSet()
    if string == 'any':
        ipset.add(netaddr.IPNetwork('0.0.0.0/0'))
    else:
        # Look for Ranges first and remove from string.
        # This allows us to reduce complexity of the ip address regex.
        ip_ranges = ip_range_regex.findall(string)
        string = ip_range_regex.sub('', string)
        ip_addresses = ip_address_regex.findall(string)
        for address in ip_addresses:
            ipset.add(address)
        for range in ip_ranges:
            ipset.add(netaddr.IPRange(range[0], range[1]))
    return ipset


def split_multiple_zones(string):
    # Test string if multiple zones
    set_identifier_regex = re.compile(r'\[|\]')
    if set_identifier_regex.search(string):
        multiple_zone = True
    else:
        multiple_zone = False

    # Test if multi-word zone
    multi_identifier_regex = re.compile(r'\"')
    if multi_identifier_regex.search(string):
        multiple_word = True
    else:
        multiple_word = False

    # Now lets logic it
    # Easy
    if not multiple_zone and not multiple_word:
        return string.split()

    # Medium
    elif multiple_zone and not multiple_word:
        return set_identifier_regex.sub('', string).split()
    elif not multiple_zone and multiple_word:
        return multi_identifier_regex.sub('', string)

    # Hard
    elif multiple_word and multiple_zone:
        multi_word_extract_regex = re.compile('(".+?")')
        special_list = multi_word_extract_regex.findall(string)
        for index, zone in enumerate(special_list):
            special_list[index] = multi_identifier_regex.sub('', zone)
        string = multi_word_extract_regex.sub('', string)
        normal_list = set_identifier_regex.sub('', string).split()
        special_list = normal_list + special_list
        return special_list

    # For Debug
    else:
        print("You Screwed Up")


def compare_dataplane_to_rules(firewall, api_key):
    # Use previous work in panexport to retrieve configuration and combined rulebase
    running_config = retrieve_firewall_configuration(firewall,
                                                     api_key,
                                                     config='running')
    pushed_config = retrieve_firewall_configuration(firewall,
                                                    api_key,
                                                    config='pushed-shared-policy')
    combined_rulebase = combine_the_rulebase(pushed_config, running_config)

    # Retrieve the raw dataplane info, debug option allows passing a text file instead to reduce API Calls.
    dataplane_raw = retrieve_dataplane(firewall, api_key)

    # Define Regex Matches
    dataplane_regex = re.compile('DP dp0:\n\n(.+)\n\nDP dp1:', re.DOTALL)
    find_rules_regex = re.compile(r"""
        "(.+?)"     # Find Rule Name
        \s          # Skip WhiteSpace
        ({.+?})     # Find Variables in Rule
        """, re.VERBOSE)
    parameters_regex = re.compile(r"""
        (\w+)       # Parameter Name
        \s          # Whitespace
        (.+?)       # Parameter
        ;           # End of Parameter String
        """, re.VERBOSE)

    # Strip \n and focus only on first dataplane
    # We are assuming dataplanes match because otherwise you need to call PaloAlto TAC Support
    dataplane_stripped = re.sub('\n', '', (dataplane_regex.search(dataplane_raw).group(1)))

    # Gather the raw_rules including parameters and create empty dictionary
    raw_rules = find_rules_regex.findall(dataplane_stripped)
    dataplane_rules = {}

    # Iterate over the raw_rules to tease out the parameters and store
    for rule in raw_rules:
        rule_name = rule[0]
        parameters = dict(parameters_regex.findall(rule[1]))
        dataplane_rules.update({rule_name: parameters})

    # Iterate over the zones in each rule to split out multiple zones specified.
    # The iteration of zones is performed first and separate from ip objects so that
    # we can throw away rules in zone filtering first.
    for rule in dataplane_rules:
        for parameter, value in dataplane_rules[rule].items():
            if (parameter == 'from') or (parameter == 'to'):
                dataplane_rules[rule][parameter] = split_multiple_zones(value)

    # Now we need to iterate over each source/destination and turn it into an IP Object.
    for rule in dataplane_rules:
        for parameter, value in dataplane_rules[rule].items():
            if (parameter == 'source') or (parameter == 'destination'):
                dataplane_rules[rule][parameter] = convert_to_ipobject(value)


def main():
    script_config = Config('config.yml')
    for firewall in script_config.firewall_hostnames:
        compare_dataplane_to_rules(firewall, script_config.firewall_api_key)


if __name__ == '__main__':
    main()