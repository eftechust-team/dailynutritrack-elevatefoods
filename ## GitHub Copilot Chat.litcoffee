## GitHub Copilot Chat

- Extension: 0.38.2 (prod)
- VS Code: 1.110.0 (0870c2a0c7c0564e7631bfed2675573a94ba4455)
- OS: win32 10.0.19045 x64
- GitHub Account: eftechust-team

## Network

User Settings:
```json
  "http.systemCertificatesNode": true,
  "github.copilot.advanced.debug.useElectronFetcher": true,
  "github.copilot.advanced.debug.useNodeFetcher": false,
  "github.copilot.advanced.debug.useNodeFetchFetcher": true
```

Connecting to https://api.github.com:
- DNS ipv4 Lookup: 20.205.243.168 (24 ms)
- DNS ipv6 Lookup: Error (9 ms): getaddrinfo ENOTFOUND api.github.com
- Proxy URL: http://127.0.0.1:18081 (2 ms)
- Proxy Connection: Error (3 ms): connect ECONNREFUSED 127.0.0.1:18081
- Electron fetch (configured): Error (2036 ms): Error: net::ERR_PROXY_CONNECTION_FAILED
	at SimpleURLLoaderWrapper.<anonymous> (node:electron/js2c/utility_init:2:10684)
	at SimpleURLLoaderWrapper.emit (node:events:519:28)
  [object Object]
  {"is_request_error":true,"network_process_crashed":false}
- Node.js https: Error (4 ms): Error: Failed to establish a socket connection to proxies: PROXY 127.0.0.1:18081
	at PacProxyAgent.<anonymous> (c:\Users\x1\AppData\Local\Programs\Microsoft VS Code\0870c2a0c7\resources\app\node_modules\@vscode\proxy-agent\out\agent.js:120:19)
	at Generator.throw (<anonymous>)
	at rejected (c:\Users\x1\AppData\Local\Programs\Microsoft VS Code\0870c2a0c7\resources\app\node_modules\@vscode\proxy-agent\out\agent.js:6:65)
	at process.processTicksAndRejections (node:internal/process/task_queues:105:5)
- Node.js fetch: Error (8 ms): TypeError: fetch failed
	at node:internal/deps/undici/undici:14902:13
	at process.processTicksAndRejections (node:internal/process/task_queues:105:5)
	at async n._fetch (c:\Users\x1\.vscode\extensions\github.copilot-chat-0.38.2\dist\extension.js:5001:4900)
	at async n.fetch (c:\Users\x1\.vscode\extensions\github.copilot-chat-0.38.2\dist\extension.js:5001:4212)
	at async d (c:\Users\x1\.vscode\extensions\github.copilot-chat-0.38.2\dist\extension.js:5033:190)
	at async Jm._executeContributedCommand (file:///c:/Users/x1/AppData/Local/Programs/Microsoft%20VS%20Code/0870c2a0c7/resources/app/out/vs/workbench/api/node/extensionHostProcess.js:494:48675)
  Error: connect ECONNREFUSED 127.0.0.1:18081
  	at TCPConnectWrap.afterConnect [as oncomplete] (node:net:1637:16)

Connecting to https://api.githubcopilot.com/_ping:
- DNS ipv4 Lookup: 140.82.114.22 (8 ms)
- DNS ipv6 Lookup: Error (5 ms): getaddrinfo ENOTFOUND api.githubcopilot.com
- Proxy URL: http://127.0.0.1:18081 (3 ms)
- Proxy Connection: Error (1 ms): connect ECONNREFUSED 127.0.0.1:18081
- Electron fetch (configured): Error (2033 ms): Error: net::ERR_PROXY_CONNECTION_FAILED
	at SimpleURLLoaderWrapper.<anonymous> (node:electron/js2c/utility_init:2:10684)
	at SimpleURLLoaderWrapper.emit (node:events:519:28)
  [object Object]
  {"is_request_error":true,"network_process_crashed":false}
- Node.js https: Error (3 ms): Error: Failed to establish a socket connection to proxies: PROXY 127.0.0.1:18081
	at PacProxyAgent.<anonymous> (c:\Users\x1\AppData\Local\Programs\Microsoft VS Code\0870c2a0c7\resources\app\node_modules\@vscode\proxy-agent\out\agent.js:120:19)
	at Generator.throw (<anonymous>)
	at rejected (c:\Users\x1\AppData\Local\Programs\Microsoft VS Code\0870c2a0c7\resources\app\node_modules\@vscode\proxy-agent\out\agent.js:6:65)
	at process.processTicksAndRejections (node:internal/process/task_queues:105:5)
- Node.js fetch: Error (9 ms): TypeError: fetch failed
	at node:internal/deps/undici/undici:14902:13
	at process.processTicksAndRejections (node:internal/process/task_queues:105:5)
	at async n._fetch (c:\Users\x1\.vscode\extensions\github.copilot-chat-0.38.2\dist\extension.js:5001:4900)
	at async n.fetch (c:\Users\x1\.vscode\extensions\github.copilot-chat-0.38.2\dist\extension.js:5001:4212)
	at async d (c:\Users\x1\.vscode\extensions\github.copilot-chat-0.38.2\dist\extension.js:5033:190)
	at async Jm._executeContributedCommand (file:///c:/Users/x1/AppData/Local/Programs/Microsoft%20VS%20Code/0870c2a0c7/resources/app/out/vs/workbench/api/node/extensionHostProcess.js:494:48675)
  Error: connect ECONNREFUSED 127.0.0.1:18081
  	at TCPConnectWrap.afterConnect [as oncomplete] (node:net:1637:16)

Connecting to https://copilot-proxy.githubusercontent.com/_ping:
- DNS ipv4 Lookup: 4.237.22.41 (25 ms)
- DNS ipv6 Lookup: Error (7 ms): getaddrinfo ENOTFOUND copilot-proxy.githubusercontent.com
- Proxy URL: http://127.0.0.1:18081 (3 ms)
- Proxy Connection: Error (1 ms): connect ECONNREFUSED 127.0.0.1:18081
- Electron fetch (configured): Error (2041 ms): Error: net::ERR_PROXY_CONNECTION_FAILED
	at SimpleURLLoaderWrapper.<anonymous> (node:electron/js2c/utility_init:2:10684)
	at SimpleURLLoaderWrapper.emit (node:events:519:28)
  [object Object]
  {"is_request_error":true,"network_process_crashed":false}
- Node.js https: Error (4 ms): Error: Failed to establish a socket connection to proxies: PROXY 127.0.0.1:18081
	at PacProxyAgent.<anonymous> (c:\Users\x1\AppData\Local\Programs\Microsoft VS Code\0870c2a0c7\resources\app\node_modules\@vscode\proxy-agent\out\agent.js:120:19)
	at Generator.throw (<anonymous>)
	at rejected (c:\Users\x1\AppData\Local\Programs\Microsoft VS Code\0870c2a0c7\resources\app\node_modules\@vscode\proxy-agent\out\agent.js:6:65)
	at process.processTicksAndRejections (node:internal/process/task_queues:105:5)
- Node.js fetch: Error (12 ms): TypeError: fetch failed
	at node:internal/deps/undici/undici:14902:13
	at process.processTicksAndRejections (node:internal/process/task_queues:105:5)
	at async n._fetch (c:\Users\x1\.vscode\extensions\github.copilot-chat-0.38.2\dist\extension.js:5001:4900)
	at async n.fetch (c:\Users\x1\.vscode\extensions\github.copilot-chat-0.38.2\dist\extension.js:5001:4212)
	at async d (c:\Users\x1\.vscode\extensions\github.copilot-chat-0.38.2\dist\extension.js:5033:190)
	at async Jm._executeContributedCommand (file:///c:/Users/x1/AppData/Local/Programs/Microsoft%20VS%20Code/0870c2a0c7/resources/app/out/vs/workbench/api/node/extensionHostProcess.js:494:48675)
  Error: connect ECONNREFUSED 127.0.0.1:18081
  	at TCPConnectWrap.afterConnect [as oncomplete] (node:net:1637:16)

Connecting to https://mobile.events.data.microsoft.com: Error (2017 ms): Error: net::ERR_PROXY_CONNECTION_FAILED
	at SimpleURLLoaderWrapper.<anonymous> (node:electron/js2c/utility_init:2:10684)
	at SimpleURLLoaderWrapper.emit (node:events:519:28)
  [object Object]
  {"is_request_error":true,"network_process_crashed":false}
Connecting to https://dc.services.visualstudio.com: Error (2033 ms): Error: net::ERR_PROXY_CONNECTION_FAILED
	at SimpleURLLoaderWrapper.<anonymous> (node:electron/js2c/utility_init:2:10684)
	at SimpleURLLoaderWrapper.emit (node:events:519:28)
  [object Object]
  {"is_request_error":true,"network_process_crashed":false}
Connecting to https://copilot-telemetry.githubusercontent.com/_ping: Error (3 ms): Error: Failed to establish a socket connection to proxies: PROXY 127.0.0.1:18081
	at PacProxyAgent.<anonymous> (c:\Users\x1\AppData\Local\Programs\Microsoft VS Code\0870c2a0c7\resources\app\node_modules\@vscode\proxy-agent\out\agent.js:120:19)
	at Generator.throw (<anonymous>)
	at rejected (c:\Users\x1\AppData\Local\Programs\Microsoft VS Code\0870c2a0c7\resources\app\node_modules\@vscode\proxy-agent\out\agent.js:6:65)
	at process.processTicksAndRejections (node:internal/process/task_queues:105:5)
Connecting to https://copilot-telemetry.githubusercontent.com/_ping: Error (2 ms): Error: Failed to establish a socket connection to proxies: PROXY 127.0.0.1:18081
	at PacProxyAgent.<anonymous> (c:\Users\x1\AppData\Local\Programs\Microsoft VS Code\0870c2a0c7\resources\app\node_modules\@vscode\proxy-agent\out\agent.js:120:19)
	at Generator.throw (<anonymous>)
	at rejected (c:\Users\x1\AppData\Local\Programs\Microsoft VS Code\0870c2a0c7\resources\app\node_modules\@vscode\proxy-agent\out\agent.js:6:65)
	at process.processTicksAndRejections (node:internal/process/task_queues:105:5)
Connecting to https://default.exp-tas.com: Error (5 ms): Error: Failed to establish a socket connection to proxies: PROXY 127.0.0.1:18081
	at PacProxyAgent.<anonymous> (c:\Users\x1\AppData\Local\Programs\Microsoft VS Code\0870c2a0c7\resources\app\node_modules\@vscode\proxy-agent\out\agent.js:120:19)
	at Generator.throw (<anonymous>)
	at rejected (c:\Users\x1\AppData\Local\Programs\Microsoft VS Code\0870c2a0c7\resources\app\node_modules\@vscode\proxy-agent\out\agent.js:6:65)
	at process.processTicksAndRejections (node:internal/process/task_queues:105:5)

Number of system certificates: 385

## Documentation

In corporate networks: [Troubleshooting firewall settings for GitHub Copilot](https://docs.github.com/en/copilot/troubleshooting-github-copilot/troubleshooting-firewall-settings-for-github-copilot).3