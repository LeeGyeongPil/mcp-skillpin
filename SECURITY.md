# 보안 정책 / Security policy

보안 취약점을 발견하셨다면 **공개 이슈 대신** 이 레포의 **Security → Report a vulnerability**(비공개 취약점 신고)로 알려 주세요. 확인 후 답변드리고, 고친 버전을 릴리스하면서 함께 안내합니다.

If you find a security issue, please **do not open a public issue**. Use **Security → Report a vulnerability** in this repository to report it privately. We will reply after checking and announce the fix with a release.

mcp-skillpin 은 CLI 입니다. 내 MCP 설정에 적힌 서버에만 나가는 방향으로 접속하고, 연습용 목 서버의 `--http` 모드만 127.0.0.1 에 열립니다. 토큰은 출력하거나 저장하지 않습니다. / mcp-skillpin is a CLI. It only makes outbound connections to servers listed in your MCP config; the practice mock server's `--http` mode binds to 127.0.0.1 only. Tokens are never printed or stored.
