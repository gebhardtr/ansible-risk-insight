# -*- mode:python; coding:utf-8 -*-

# Copyright (c) 2022 IBM Corp. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import argparse
import os
import logging
from typing import List
from tabulate import tabulate

from ansible_risk_insight.models import TaskCallsInTree
from .keyutil import detect_type, key_delimiter
from .analyzer import load_taskcalls_in_trees
from .rules.base import subject_placeholder
from ansible_risk_insight import rules


def indent(multi_line_txt, level=0):
    lines = multi_line_txt.splitlines()
    lines = [" " * level + line for line in lines if line.replace(" ", "") != ""]
    return "\n".join(lines)


def key2name(key: str):
    _type = detect_type(key)
    if _type == "playbook":
        return os.path.basename(key.split(key_delimiter)[-1])
    elif _type == "role":
        return key.split(key_delimiter)[-1]


def load_rules():
    _rules = []
    for rule in rules.__all__:
        _rules.append(getattr(rules, rule)())
    return _rules


def make_subject_str(playbook_num: int, role_num: int):
    subject = ""
    if playbook_num > 0 and role_num > 0:
        subject = "playbooks/roles"
    elif playbook_num > 0:
        subject = "playbooks"
    elif role_num > 0:
        subject = "roles"
    return subject


def detect(taskcalls_in_trees: List[TaskCallsInTree], collection_name: str = ""):
    rules = load_rules()
    extra_check_args = {}
    if collection_name != "":
        extra_check_args["collection_name"] = collection_name
    result_txt = ""
    result_txt += "-" * 90 + "\n"
    result_txt += "Ansible Risk Insight Report\n"
    result_txt += "-" * 90 + "\n"
    report_num = 1

    playbook_count = {"total": 0, "risk": 0}
    role_count = {"total": 0, "risk": 0}

    data_report = {"summary": {}, "details": []}
    separate_report = {}
    role_to_playbook_mappings = {}
    risk_found_playbooks = set()

    tmp_result_txt = ""
    num = len(taskcalls_in_trees)
    result_dict = {}
    for i, taskcalls_in_tree in enumerate(taskcalls_in_trees):
        if not isinstance(taskcalls_in_tree, TaskCallsInTree):
            continue
        tree_root_key = taskcalls_in_tree.root_key
        tree_root_type = detect_type(tree_root_key)
        tree_root_name = key2name(tree_root_key)

        is_playbook = tree_root_type == "playbook"
        if is_playbook:
            playbook_count["total"] += 1

            taskcalls = taskcalls_in_tree.taskcalls
            for taskcall in taskcalls:
                parts = taskcall.spec.defined_in.split("/")
                if parts[0] == "roles":
                    role_name = parts[1]
                    _mappings = role_to_playbook_mappings.get(role_name, [])
                    if tree_root_name not in _mappings:
                        _mappings.append(tree_root_name)
                    role_to_playbook_mappings[role_name] = _mappings
        else:
            role_count["total"] += 1

        do_report = False
        taskcalls = taskcalls_in_tree.taskcalls
        tmp_result_txt_alt = ""
        for rule in rules:
            if not rule.enabled:
                continue
            rule_name = rule.name
            matched, _, message = rule.check(taskcalls, **extra_check_args)
            if rule.separate_report:
                if rule_name not in separate_report:
                    separate_report[rule_name] = {
                        "rule": rule,
                        "matched": [],
                    }
            if matched:
                if rule.separate_report:
                    tree_root_label = tree_root_type
                    separate_report[rule_name]["matched"].append([tree_root_label, tree_root_name, message])

                    if rule_name not in result_dict:
                        result_dict[rule_name] = []
                    result_dict[rule_name].append(
                        {
                            "type": tree_root_type,
                            "name": tree_root_name,
                            "message": message,
                        }
                    )
                else:
                    if not is_playbook:
                        do_report = True
                        tmp_result_txt_alt += rule_name + "\n"
                        tmp_result_txt_alt += indent(message, 0) + "\n"

                        used_in_playbooks = role_to_playbook_mappings.get(tree_root_name, [])

                        if rule_name not in result_dict:
                            result_dict[rule_name] = []
                        result_dict[rule_name].append(
                            {
                                "type": tree_root_type,
                                "name": tree_root_name,
                                "message": message,
                                "playbooks_use_this_role": used_in_playbooks,
                            }
                        )
        if do_report and tmp_result_txt_alt != "":
            tmp_result_txt += "#{} {} - {}\n".format(report_num, tree_root_type.upper(), tree_root_name)
            used_in_playbooks = role_to_playbook_mappings.get(tree_root_name, [])
            risk_found_playbooks = risk_found_playbooks.union(set(used_in_playbooks))
            if len(used_in_playbooks) > 0:
                tmp_result_txt += "(used_in: {})\n".format(used_in_playbooks)
            tmp_result_txt += tmp_result_txt_alt
            tmp_result_txt += "-" * 90 + "\n"
            report_num += 1
            if is_playbook:
                playbook_count["risk"] += 1
            else:
                role_count["risk"] += 1
        logging.debug("detect() {}/{} done".format(i + 1, num))
    for rule_name in result_dict:
        results = result_dict[rule_name]
        data_report["details"].append({"rule": rule_name, "results": results})

    if playbook_count["total"] > 0:
        result_txt += "Playbooks\n"
        result_txt += "  Total: {}\n".format(playbook_count["total"])
        result_txt += "  Risk Found: {}\n".format(len(risk_found_playbooks))

        data_report["summary"]["playbooks"] = {
            "total": playbook_count["total"],
            "risk_found": playbook_count["risk"],
        }
    if role_count["total"] > 0:
        result_txt += "Roles\n"
        result_txt += "  Total: {}\n".format(role_count["total"])
        result_txt += "  Risk Found: {}\n".format(role_count["risk"])

        data_report["summary"]["roles"] = {
            "total": role_count["total"],
            "risk_found": role_count["risk"],
        }
    result_txt += "-" * 90 + "\n"

    result_txt += tmp_result_txt

    for label, rule_data in separate_report.items():
        rule = rule_data["rule"]
        table_data = rule_data["matched"]
        result_txt += label + "\n"
        placeholder = subject_placeholder
        subject = make_subject_str(playbook_count["total"], role_count["total"])
        table_txt = "  All {} are OK".format(placeholder)
        if rule.all_ok_message != "":
            table_txt = "  {}".format(rule.all_ok_message)
        if len(table_data) > 0:
            table_txt = tabulate(table_data, tablefmt="plain")
        else:
            table_txt = table_txt.replace(placeholder, subject)
        result_txt += indent(table_txt, 0) + "\n"
        result_txt += "-" * 90 + "\n"
    return result_txt, data_report


def main():
    parser = argparse.ArgumentParser(
        prog="risk_detector.py",
        description="Detect risks from tasks by checking rules",
        epilog="end",
        add_help=True,
    )

    parser.add_argument(
        "-i",
        "--input",
        default="",
        help="path to the input json (tasks_in_trees.json)",
    )
    parser.add_argument("-o", "--output", default="", help="path to the output json")
    parser.add_argument("-v", "--verbose", default="", help="show details during the process")

    args = parser.parse_args()

    tasks_in_trees = load_taskcalls_in_trees(args.input)

    detect(tasks_in_trees)


if __name__ == "__main__":
    main()
