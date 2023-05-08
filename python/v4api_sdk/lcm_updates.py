"""
Use the Nutanix v4 API SDKs to gather a list of upgradeable LCM entities,
then generate an LCM update plan
"""

import getpass
import argparse
import time
import sys
from pprint import pprint
import urllib3
from timeit import default_timer as timer

# only the ntnx_lcm_py_client namespace is required for this code sample
import ntnx_lcm_py_client
from ntnx_lcm_py_client import ApiClient as LCMClient
from ntnx_lcm_py_client import Configuration as LCMConfiguration
from ntnx_lcm_py_client.rest import ApiException as LCMException

# required for building the list of LCM components that can be updated
from ntnx_lcm_py_client.Ntnx.lcm.v4.common.PrecheckSpec import PrecheckSpec
from ntnx_lcm_py_client.Ntnx.lcm.v4.common.EntityUpdateSpec import EntityUpdateSpec
from ntnx_lcm_py_client.Ntnx.lcm.v4.common.EntityUpdateSpecs import EntityUpdateSpecs
from ntnx_lcm_py_client.Ntnx.lcm.v4.resources.RecommendationSpec import RecommendationSpec
from ntnx_lcm_py_client.Ntnx.lcm.v4.common.UpdateSpec import UpdateSpec

# small library that manages commonly-used tasks across these code samples
from tme import Utils


# function to display the progress of an LCM task every "poll_timeout" seconds
# useful for long-running tasks such as LCM inventory or pre-check
def monitor_task_lcm(task_ext_id, task_name, client, poll_timeout):
    """
    method used to monitor Prism Central tasks
    will print a series of period characters and re-check task
    status at the specified interval
    this version uses the LCM SDK
    """
    start = timer()
    # print message until specified  task is finished
    lcm_instance = ntnx_lcm_py_client.api.TaskApi(api_client=client)
    task = lcm_instance.get_task_by_uuid(task_ext_id)
    units = "second" if poll_timeout == 1 else "seconds"
    print(
        f"{task_name} running, checking progress every \
{poll_timeout} {units} ...",
        end="",
    )
    while True:
        if task.data["status"] == "kRunning":
            print(".", end="", flush=True)
        else:
            print(" finished.")
            break
        time.sleep(int(poll_timeout))
        task = lcm_instance.get_task_by_uuid(task_ext_id)
    end = timer()
    elapsed_time = end - start
    if elapsed_time <= 60:
        duration = f"{round(elapsed_time, 0)} second(s)"
    else:
        duration = f"{round(elapsed_time // 60, 0)} minute(s)"
    return(duration)


def main():
    """
    suppress warnings about insecure connections
    consider the security implications before
    doing this in a production environment
    """
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    """
    setup the command line parameters
    for this example only two parameters are required
    - the Prism Central IP address or FQDN
    - the Prism Central username; the script will prompt for the user's
      password so that it never needs to be stored in plain text
    - the time in seconds between task polling
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("pc_ip", help="Prism Central IP address or FQDN")
    parser.add_argument("username", help="Prism Central username")
    parser.add_argument(
        "-p", "--poll", help="Time between task polling, in seconds", default=1
    )
    args = parser.parse_args()

    # get the cluster password
    cluster_password = getpass.getpass(
        prompt="Enter your password: ",
        stream=None,
    )

    # grab the command-line parameters from the args object
    pc_ip = args.pc_ip
    username = args.username
    poll_timeout = args.poll

    # verify the user has entered a password
    if not cluster_password:
        while not cluster_password:
            print(
                "Password cannot be empty.  \
    Enter a password or Ctrl-C/Ctrl-D to exit."
            )
            cluster_password = getpass.getpass(
                prompt="Enter your password: ", stream=None
            )

    # build the connection configuration
    lcm_config = LCMConfiguration()
    lcm_config.host = pc_ip
    lcm_config.username = username
    lcm_config.password = cluster_password
    lcm_config.verify_ssl = False

    try:
        # create utils instance for re-use later
        utils = Utils(pc_ip=pc_ip, username=username, password=cluster_password)
        lcm_client = LCMClient(configuration=lcm_config)
        lcm_client.add_default_header(
            header_name="Accept-Encoding", header_value="gzip, deflate, br"
        )

        # the confirm function simply asks for a yes/NO confirmation before
        # continuing with the specified action
        run_inventory = utils.confirm(
            "Run LCM Inventory?  This can take some time, depending on \
environment configuration."
        )
        if run_inventory:
            # start an LCM inventory
            lcm_instance = ntnx_lcm_py_client.api.InventoryApi(api_client=lcm_client)
            print("Starting LCM inventory ...")
            inventory = lcm_instance.inventory(async_req=False)
            # grab the unique identifier for the LCM inventory task
            inventory_task_ext_id = inventory.data.ext_id
            inventory_duration = monitor_task_lcm(
                task_ext_id=inventory_task_ext_id,
                task_name="Inventory",
                client=lcm_client,
                poll_timeout=poll_timeout,
            )
            print(f"Inventory duration: {inventory_duration}.")
        else:
            print("LCM Inventory skipped.")

        run_precheck = utils.confirm(
            "Run precheck?  This can take some time, depending on environment \
configuration. Note Inventory may need to be run before LCM Precheck \
can be completed."
        )
        if run_precheck:
            # kick off LCM pre-checks
            # note inventory will do this for you, unless specified otherwise
            precheck_spec = PrecheckSpec()
            precheck_spec.entity_update_specs = []
            lcm_instance = ntnx_lcm_py_client.api.PrecheckApi(api_client=lcm_client)
            print("Starting LCM pre-checks ...")
            precheck = lcm_instance.precheck(async_req=False, body=precheck_spec)
            precheck_task_ext_id = precheck.data.ext_id
            precheck_duration = monitor_task_lcm(
                task_ext_id=precheck_task_ext_id,
                task_name="Precheck",
                client=lcm_client,
                poll_timeout=poll_timeout,
            )
            print(f"Precheck duration: {precheck_duration}.")
        else:
            print("Precheck skipped.")

        # gather a list of supported entities
        # this will be used to show human-readable update info shortly
        lcm_instance = ntnx_lcm_py_client.api.EntityApi(api_client=lcm_client)
        print("Getting Supported Entity list ...")
        entities = lcm_instance.get_entities(async_req=False)

        """
        gather LCM update recommendations
        IMPORTANT NOTE: the way recommendations are collected will change
        before the v4 LCM APIs and SDKs are released as Generally Available
        (GA); this script should be used for demonstration purposes only
        """
        lcm_instance = ntnx_lcm_py_client.api.RecommendationsApi(api_client=lcm_client)
        print("Getting LCM Recommendations ...")
        rec_spec = RecommendationSpec()
        # specify that this script should only look for available software updates
        rec_spec.entity_types = ["software"]
        recommendations = lcm_instance.get_recommendations(
            async_req=False, body=rec_spec
        )
        update_info = []
        for rec in recommendations.data["entityUpdateSpecs"]:
            entity_matches = [
                entity for entity in entities.data if entity.uuid == rec["entityUuid"]
            ]
            if len(entity_matches) > 0:
                update_info.append(
                    {
                        "product_name": entity_matches[0].entity_model,
                        "version": entity_matches[0].version,
                        "entity_uuid": entity_matches[0].uuid,
                    }
                )
        print(
            f"{len(recommendations.data['entityUpdateSpecs'])} software \
components can be updated:"
        )
        pprint(update_info)

        if not update_info:
            print("No updates available, skipping LCM Update planning.\n")
        else:
            # generate update plan
            # for this demo we'll do this for all recommendations returned
            # in the previous request
            lcm_instance = ntnx_lcm_py_client.api.PlanApi(api_client=lcm_client)
            print("Generating LCM Update Plan ...")
            entity_update_specs = EntityUpdateSpecs()
            entity_update_specs.entity_update_specs = []
            for recommendation in recommendations.data["entityUpdateSpecs"]:
                spec = EntityUpdateSpec()
                spec.entity_uuid = recommendation["entityUuid"]
                spec.version = recommendation["version"]
                entity_update_specs.entity_update_specs.append(spec)
            if len(entity_update_specs.entity_update_specs) > 0:
                plan = lcm_instance.get_plan(async_req=False, body=entity_update_specs)
                print(
                    f"{len(plan.data.host_upgrade_plans)} host update plans generated:"
                )
                pprint(plan.data.host_upgrade_plans)
            else:
                print("No host upgrade plans or updates available.")
                sys.exit()

            # make sure there are entities to update
            if entity_update_specs.entity_update_specs is not None:
                print(
                    f"{len(entity_update_specs.entity_update_specs)} \
updates available."
                )
                # make sure the user wants to install updates
                install_updates = utils.confirm("Install updates?")
                if install_updates:
                    # do the actual update
                    lcm_instance = ntnx_lcm_py_client.api.UpdateApi(
                        api_client=lcm_client
                    )
                    print("Updating software via LCM ...")
                    update_spec = UpdateSpec()
                    # configure the update properties, timing etc
                    update_spec.entity_update_specs = (
                        entity_update_specs.entity_update_specs
                    )
                    # skip the pinned VM prechecks
                    # WARNING: consider the implications of doing this in production
                    update_spec.skipped_precheck_flags = ["powerOffUvms"]
                    update_spec.wait_in_sec_for_app_up = 60
                    plan = lcm_instance.update(async_req=False, body=update_spec)
                    update_task_ext_id = plan.data.ext_id
                    update_duration = monitor_task_lcm(
                        task_ext_id=update_task_ext_id,
                        task_name="Update",
                        client=lcm_client,
                        poll_timeout=poll_timeout,
                    )
                    print(f"Update duration: {update_duration}.")
                else:
                    print("Updates cancelled.")
            else:
                print(
                    "No updates available at this time.  Make sure \
available updates weren't skipped due to development script exclusions."
                )

    except LCMException as lcm_exception:
        print(
            f"Unable to complete the requested action.  See below for \
additional details.  \
Exception details: {lcm_exception}"
        )


if __name__ == "__main__":
    main()