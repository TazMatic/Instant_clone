#!/usr/bin/env python
import atexit
import requests.packages.urllib3 as urllib3
import ssl
import argparse
import getpass

from pyVmomi import vim
from pyVmomi import vmodl
from pyVim.connect import SmartConnect, Disconnect

def wait_for_tasks(service_instance, tasks):
    """Given the service instance si and tasks, it returns after all the
   tasks are complete
   """
    property_collector = service_instance.content.propertyCollector
    task_list = [str(task) for task in tasks]
    # Create filter
    obj_specs = [vmodl.query.PropertyCollector.ObjectSpec(obj=task)
                 for task in tasks]
    property_spec = vmodl.query.PropertyCollector.PropertySpec(type=vim.Task,
                                                               pathSet=[],
                                                               all=True)
    filter_spec = vmodl.query.PropertyCollector.FilterSpec()
    filter_spec.objectSet = obj_specs
    filter_spec.propSet = [property_spec]
    pcfilter = property_collector.CreateFilter(filter_spec, True)
    try:
        version, state = None, None
        # Loop looking for updates till the state moves to a completed state.
        while len(task_list):
            update = property_collector.WaitForUpdates(version)
            for filter_set in update.filterSet:
                for obj_set in filter_set.objectSet:
                    task = obj_set.obj
                    for change in obj_set.changeSet:
                        if change.name == 'info':
                            state = change.val.state
                        elif change.name == 'info.state':
                            state = change.val
                        else:
                            continue

                        if not str(task) in task_list:
                            continue

                        if state == vim.TaskInfo.State.success:
                            # Remove task from taskList
                            task_list.remove(str(task))
                        elif state == vim.TaskInfo.State.error:
                            raise task.info.error
            # Move to next version
            version = update.version
    finally:
        if pcfilter:
pcfilter.Destroy()

def build_arg_parser():
    """
    Builds a standard argument parser with arguments for talking to vCenter
    -s service_host_name_or_ip
    -o optional_port_number
    -u required_user
    -p optional_password
    """
    parser = argparse.ArgumentParser(
        description='Standard Arguments for talking to vCenter')

    # because -h is reserved for 'help' we use -s for service
    parser.add_argument('-s', '--host',
                        required=True,
                        action='store',
                        help='vSphere service to connect to')

    # because we want -p for password, we use -o for port
    parser.add_argument('-o', '--port',
                        type=int,
                        default=443,
                        action='store',
                        help='Port to connect on')

    parser.add_argument('-u', '--user',
                        required=True,
                        action='store',
                        help='User name to use when connecting to host')

    parser.add_argument('-p', '--password',
                        required=False,
                        action='store',
                        help='Password to use when connecting to host')

    parser.add_argument('-S', '--disable_ssl_verification',
                        required=False,
                        action='store_true',
                        help='Disable ssl host certificate verification')

    return parser


def prompt_for_password(args):
    """
    if no password is specified on the command line, prompt for it
    """
    if not args.password:
        args.password = getpass.getpass(
            prompt='Enter password for host %s and user %s: ' %
                   (args.host, args.user))
    return args



def get_args():
    parser = build_arg_parser()
    parser.add_argument('-v', '--vm_name',
                        required=True,
                        action='store',
                        help='Name of the new VM')

    parser.add_argument('--template_name',
                        required=True,
                        action='store',
                        help='Name of the template/VM you are cloning from')

    parser.add_argument('-n', '--number_of_clones,
                        required=False,
                        action='store',
                        default=1,
                        help='Number of clones to make.')
    
    parser.add_argument('--datacenter_name',
                        required=False,
                        action='store',
                        default=None,
                        help='Name of the Datacenter you wish to use.')

    parser.add_argument('--cluster_name',
                        required=False,
                        action='store',
                        default=None,
                        help='Name of the cluster you wish to use')

    parser.add_argument('--host_name',
                        required=False,
                        action='store',
                        default=None,
                        help='IP of vcenter you wish to use')

    args = parser.parse_args()

    prompt_for_password(args)
    return args


def get_obj(content, vimtype, name, folder=None):
    obj = None
    if not folder:
        folder = content.rootFolder
    container = content.viewManager.CreateContainerView(folder, vimtype, True)
    for item in container.view:
        if item.name == name:
            obj = item
            break
    return obj


def _clone_vm(si, template, vm_name, vm_folder, location):
    clone_spec = vim.vm.CloneSpec(
        powerOn=True, template=False, location=location,
        snapshot=template.snapshot.rootSnapshotList[0].snapshot)
    task = template.Clone(name=vm_name, folder=vm_folder, spec=clone_spec)
    wait_for_tasks(si, [task])
    print "Successfully cloned and created the VM '{}'".format(vm_name)


def _get_relocation_spec(host, resource_pool):
    relospec = vim.vm.RelocateSpec()
    relospec.diskMoveType = 'createNewChildDiskBacking'
    relospec.host = host
    relospec.pool = resource_pool
    return relospec


def _take_template_snapshot(si, vm):
    if len(vm.rootSnapshot) < 1:
        task = vm.CreateSnapshot_Task(name='test_snapshot',
                                      memory=False,
                                      quiesce=False)
        wait_for_tasks(si, [task])
        print "Successfully taken snapshot of '{}'".format(vm.name)


def main():
    args = get_args()
    urllib3.disable_warnings()
    si = None
    context = None
    if hasattr(ssl, 'SSLContext'):
        context = ssl.SSLContext(ssl.PROTOCOL_SSLv23)
        context.verify_mode = ssl.CERT_NONE
    if context:
        # Python >= 2.7.9
        si = SmartConnect(host=args.host,
                          port=int(args.port),
                          user=args.user,
                          pwd=args.password,
                          sslContext=context)
    else:
        # Python >= 2.7.7
        si = SmartConnect(host=args.host,
                          port=int(args.port),
                          user=args.user,
                          pwd=args.password)
    atexit.register(Disconnect, si)
    print "Connected to vCenter Server"

    content = si.RetrieveContent()

    datacenter = get_obj(content, [vim.Datacenter], args.datacenter_name)
    if not datacenter:
        raise Exception("Couldn't find the Datacenter with the provided name "
                        "'{}'".format(args.datacenter_name))

    cluster = get_obj(content, [vim.ClusterComputeResource], args.cluster_name,
                      datacenter.hostFolder)

    if not cluster:
        raise Exception("Couldn't find the Cluster with the provided name "
                        "'{}'".format(args.cluster_name))

    host_obj = None
    for host in cluster.host:
        if host.name == args.host_name:
            host_obj = host
            break

    vm_folder = datacenter.vmFolder

    template = get_obj(content, [vim.VirtualMachine], args.template_name,
                       vm_folder)

    if not template:
        raise Exception("Couldn't find the template with the provided name "
                        "'{}'".format(args.template_name))

    for clone_number in range(1, args.number_of_clones):
        location = _get_relocation_spec(host_obj, cluster.resourcePool)
        _take_template_snapshot(si, template)
        _clone_vm(si, template, args.vm_name + str(x), vm_folder, location)

if __name__ == "__main__":
main()
