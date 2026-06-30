from types import SimpleNamespace


class FakeMetaApiAccount:
    def __init__(self, account_id: str, *, state: str = "UNDEPLOYED") -> None:
        self.id = account_id
        self.state = state
        self.deploy_calls = 0
        self.wait_deployed_calls = 0
        self.wait_connected_calls = 0
        self.undeploy_calls = 0
        self.remove_calls = 0

    async def deploy(self) -> None:
        self.deploy_calls += 1
        self.state = "DEPLOYING"

    async def wait_deployed(self, **_kwargs) -> None:
        self.wait_deployed_calls += 1
        self.state = "DEPLOYED"

    async def wait_connected(self, **_kwargs) -> None:
        self.wait_connected_calls += 1

    async def undeploy(self) -> None:
        self.undeploy_calls += 1

    async def remove(self) -> None:
        self.remove_calls += 1


class FakeMetaApi:
    def __init__(self, account: FakeMetaApiAccount) -> None:
        self.metatrader_account_api = SimpleNamespace(get_account=self.get_account)
        self.account = account

    async def get_account(self, account_id: str) -> FakeMetaApiAccount:
        assert account_id == self.account.id
        return self.account


class FakeStreamingConnection:
    def __init__(self) -> None:
        self.connect_calls = 0
        self.wait_synchronized_calls = 0
        self.close_calls = 0
        self.terminal_state = SimpleNamespace(
            specifications=[], positions=[], orders=[]
        )

    async def connect(self) -> None:
        self.connect_calls += 1

    async def wait_synchronized(self, _options: dict) -> None:
        self.wait_synchronized_calls += 1

    async def close(self) -> None:
        self.close_calls += 1


class FakeStreamingAccount:
    def __init__(self, account_id: str) -> None:
        self.id = account_id
        self.created_connections: list[FakeStreamingConnection] = []

    def get_streaming_connection(self) -> FakeStreamingConnection:
        connection = FakeStreamingConnection()
        self.created_connections.append(connection)
        return connection


class FakeStreamingApi:
    def __init__(self, account: FakeStreamingAccount) -> None:
        self.account = account
        self.get_account_calls = 0
        self.metatrader_account_api = SimpleNamespace(get_account=self.get_account)

    async def get_account(self, account_id: str) -> FakeStreamingAccount:
        assert account_id == self.account.id
        self.get_account_calls += 1
        return self.account


class FakeProvisioningClient:
    def __init__(self, responses: list[dict | None]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[dict, str]] = []

    async def create_account(self, payload: dict, transaction_id: str) -> dict | None:
        self.calls.append((payload, transaction_id))
        return self.responses.pop(0)
