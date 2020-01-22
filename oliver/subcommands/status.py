import argparse
import datetime
import pendulum

from collections import defaultdict
from logzero import logger
from typing import Dict, List, Optional
from tzlocal import get_localzone

from .. import api, batch, constants, reporting, utils


def call(args: Dict):
    """Execute the subcommand.
    
    Args:
        args (Dict): Arguments parsed from the command line.
    """

    cromwell = api.CromwellAPI(
        server=args["cromwell_server"], version=args["cromwell_api_version"],
    )

    statuses = get_statuses_to_query(args)

    labels = []
    if "job_name" in args and args["job_name"]:
        labels.append(f"{constants.OLIVER_JOB_NAME_KEY}:{args['job_name']}")

    if "job_group" in args and args["job_group"]:
        labels.append(f"{constants.OLIVER_JOB_GROUP_KEY}:{args['job_group']}")

    submission = None
    if "submission_time" in args and args["submission_time"] > 0:
        submission = (
            datetime.datetime.now() - datetime.timedelta(hours=args["submission_time"])
        ).replace(microsecond=0).isoformat("T") + "Z"

    workflows = sorted(
        cromwell.get_workflows_query(
            includeSubworkflows=False,
            labels=labels,
            ids=[args["workflow_id"]],
            names=[args["workflow_name"]],
            submission=submission,
        ),
        key=lambda k: k["submission"],
    )

    if args["batch_number_ago"] is not None:
        workflows = batch.batch_workflows(workflows, args["batch_interval_mins"])
        max_batch_number = max([w["batch"] for w in workflows])
        batch_number_to_target = max_batch_number - args["batch_number_ago"]
        logger.info(f"Targetting all jobs in batch {batch_number_to_target}.")
        workflows = list(
            filter(lambda x: x["batch"] == batch_number_to_target, workflows)
        )

    if statuses:
        workflows = list(filter(lambda x: x["status"] in statuses, workflows))

    metadatas = {w["id"]: cromwell.get_workflows_metadata(w["id"]) for w in workflows}
    print_workflow_summary(workflows, metadatas, grid_style=args["grid_style"])
    if "detail" in args and args["detail"]:
        print()
        print_workflow_detail(workflows, metadatas, grid_style=args["grid_style"])


def register_subparser(subparser: argparse._SubParsersAction):
    """Registers a subparser for the current command.
    
    Args:
        subparser (argparse._SubParsersAction): Subparsers action.
    """

    subcommand = subparser.add_parser(
        "status",
        aliases=["st"],
        help="Report various statistics about a running Cromwell server.",
    )
    subcommand.add_argument(
        "-a",
        "--aborted",
        dest="show_aborted_statuses",
        help="Show jobs in the 'Aborted' state.",
        default=False,
        action="store_true",
    )
    subcommand.add_argument(
        "-b",
        "--batch-number-ago",
        help="(experimental) Show outputs from N batches ago.",
        default=None,
        type=int,
    )
    subcommand.add_argument(
        "--batch-interval-mins",
        help="(experimental) Split batches by any two jobs separated by N minutes.",
        default=5,
        type=int,
    )
    subcommand.add_argument(
        "-d",
        "--detail",
        help="Show detailed view.",
        default=False,
        action="store_true",
    )
    subcommand.add_argument(
        "-f",
        "--failed",
        dest="show_failed_statuses",
        help="Show jobs in the 'Failed' state.",
        default=False,
        action="store_true",
    )
    subcommand.add_argument(
        "-g", "--job-group", help="Job Group", type=str, default=None
    )
    subcommand.add_argument(
        "-i", "--workflow-id", type=str, help="Filter by workflow id matching argument."
    )
    subcommand.add_argument("-j", "--job-name", help="Job Name", type=str, default=None)
    subcommand.add_argument(
        "-n",
        "--workflow-name",
        type=str,
        help="Filter by workflow name matching argument.",
    )
    subcommand.add_argument(
        "-r",
        "--running",
        dest="show_running_statuses",
        help="Show jobs in the 'Running' state.",
        default=False,
        action="store_true",
    )
    subcommand.add_argument(
        "--submission-time",
        help="Show only jobs which were submitted at most N hours ago.",
        default=24,
        type=int,
    )
    subcommand.add_argument(
        "-s",
        "--succeeded",
        dest="show_succeeded_statuses",
        help="Show jobs in the 'Succeeded' state.",
        default=False,
        action="store_true",
    )
    subcommand.add_argument(
        "--grid-style",
        help="Any valid `tablefmt` for python-tabulate.",
        default="fancy_grid",
    )
    subcommand.set_defaults(func=call)


def get_statuses_to_query(args: Dict) -> Optional[List[str]]:
    """Get a list of statues to consider when querying for workflow.
    
    Args:
        args (Dict): Arguments parsed from the command line.
    
    Returns:
        Optional[List[str]]: List of statuses to consider or None.
    """

    if (
        args["show_running_statuses"]
        or args["show_aborted_statuses"]
        or args["show_failed_statuses"]
        or args["show_succeeded_statuses"]
    ):

        statuses = []
        if args["show_running_statuses"]:
            statuses.append("Running")

        if args["show_aborted_statuses"]:
            statuses.append("Aborted")

        if args["show_failed_statuses"]:
            statuses.append("Failed")

        if args["show_succeeded_statuses"]:
            statuses.append("Succeeded")

        return statuses

    return None


def print_workflow_summary(workflows: List, metadatas: Dict, grid_style="fancy_grid"):
    """Print a summary of workflow statuses.
    
    Args:
        workflows (List): List of workflows returned from the API call.
        metadatas (Dict): Dictionary of metadatas indexed by workflow id.
    """

    agg = defaultdict(lambda: defaultdict(int))

    for w in workflows:
        m = metadatas[w["id"]]
        job_group = utils.get_oliver_group(m)
        if not job_group:
            job_group = "<not set>"

        agg[job_group][m["status"]] += 1

    results = []
    keys = set()
    for group in agg.keys():
        for k in agg[group]:
            keys.add(k)
        obj = {"Group Name": group}
        obj.update(agg[group])
        results.append(obj)

    for r in results:
        for k in keys:
            if not k in r:
                r[k] = 0

    reporting.print_dicts_as_table(results, grid_style)


def print_workflow_detail(workflows: List, metadatas: Dict, grid_style="fancy_grid"):
    """Print a detailed table of workflow statuses.
    
    Args:
        workflows (List): List of workflows returned from the API call.
        metadatas (Dict): Dictionary of metadatas indexed by workflow id.
    """

    results = [
        {
            "Workflow ID": w["id"] if "id" in w else "",
            "Workflow Name": w["name"] if "name" in w else "",
            "Status": w["status"] if "status" in w else "",
            "Start": pendulum.parse(w["start"])
            .in_tz(get_localzone())
            .to_day_datetime_string()
            if "start" in w
            else "",
        }
        for w in workflows
    ]

    reporting.print_dicts_as_table(results, grid_style)
