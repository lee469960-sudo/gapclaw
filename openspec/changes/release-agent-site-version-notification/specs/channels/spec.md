## ADDED Requirements

### Requirement: A dedicated Feishu channel can be bound to Release Agent

The channel configuration MUST support creating a separate Feishu bot instance using the existing Feishu credentials, webhook verification, and delivery protocol, and binding that instance to the built-in Release Agent. The binding MUST be explicit and MUST NOT silently redirect ordinary Agent conversations to the release bot.

#### Scenario: Configure the Release Agent Feishu bot

- **WHEN** an administrator creates a Feishu channel and selects Release Agent as its bound agent
- **THEN** the system stores the channel binding and exposes its verified webhook configuration
- **AND** only Release Agent release notifications are routed to that dedicated channel

#### Scenario: Existing Feishu channels remain unchanged

- **WHEN** a new Release Agent Feishu channel is added
- **THEN** existing Feishu channel bindings continue routing to their original Agents
- **AND** their credentials and webhook paths are not replaced

#### Scenario: Unconfigured Release Agent channel

- **WHEN** a release transition occurs without an enabled Release Agent Feishu channel
- **THEN** deployment state and site-version synchronization continue normally
- **AND** the missing notification channel is recorded as a delivery warning rather than a deployment failure
