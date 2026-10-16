#commons.py //2024年3月5日23点25分

from utils.colors import Color
from utils.wechatutils import WechatUtils
import frida,sys


class Commons:
    def __init__(self):
        self.wechatutils_instance = WechatUtils()
        self.device = frida.get_local_device()
        self.process = self.device.enumerate_processes()
        self.pid = -1
        self.version_list = []
        self.configs_path = ""

    def onMessage(self, message, data):
        if message['type'] == 'send':
            print(Color.GREEN + message['payload'], Color.END)
        elif message['type'] == 'error':
            print(Color.RED + message['stack'], Color.END)

    def inject_wehcatEx(self, pid, code):
        session = frida.attach(pid)
        script = session.create_script(code)
        script.on("message", self.onMessage)
        script.load()
        import time
        if sys.stdin is not None and sys.stdin.isatty():
            sys.stdin.read()
        else:
            while True:
                time.sleep(60)


    def load_wechatEx_configs(self):
        path = self.wechatutils_instance.get_configs_path()
        pid, version = self.wechatutils_instance.get_wechat_pid_and_version_mac()
        print(pid, version)
        if pid or version is not None:
            wehcatEx_hookcode = self.build_hookcode(path, version)
            self.inject_wehcatEx(pid, wehcatEx_hookcode)
        else:
            self.wechatutils_instance.print_process_not_found_message()

    def build_hookcode(self, path, version):
        """Electron-based 4.1.13+ uses switch-baked hook; legacy keeps old flow."""
        switch_based = {"f2641d62"}
        if version in switch_based:
            return open(path + "../scripts/hook_v413.js", "r", encoding="utf-8").read()
        wehcatEx_hookcode = open(path + "../scripts/hook.js", "r", encoding="utf-8").read()
        wechatEx_addresses = open(path + "../configs/address_{}_arm.json".format(version)).read()
        return "var address=" + wechatEx_addresses + wehcatEx_hookcode

    def load_wechatEx_configs_pid(self,pid):
        path = self.wechatutils_instance.get_configs_path()
        version = self.wechatutils_instance.get_wechat_version_mac()
        if pid or version is not None:
            wehcatEx_hookcode = self.build_hookcode(path, version)
            self.inject_wehcatEx(pid, wehcatEx_hookcode)
        else:
            self.wechatutils_instance.print_process_not_found_message()
