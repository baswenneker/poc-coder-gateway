# Coder Gateway

An OpenAI-compatible gateway between a coding agent and an upstream model. It watches every Conversation and gently steers it towards the team's way of working.

Why it exists: developers get better and more consistent code without having to configure their coding agent well. The Gateway supplies that knowledge instead, by following the Conversation and steering it where needed. Every design choice is judged against this purpose.

## Language

### Setup

**Gateway**:
The service that sits between the coding agent and the upstream model and decides for every request whether to intervene.
_Avoid_: Proxy, middleware

**Virtual Model**:
What the coding agent sees as "the model" behind one API key: a real upstream model plus a system prompt plus a Workflow Definition.
_Avoid_: Profile, persona

**Workflow Definition**:
The set of Rules a team wants its developers to follow, attached to a Virtual Model.
_Avoid_: Policy, workflow config

**Rule**:
One expectation in a Workflow Definition, together with the Intervention it triggers when broken.
_Avoid_: Check, guard

### People and conversations

**Developer**:
The human working with the coding agent. Only the Developer answers a Proposal, never the agent itself.
_Avoid_: User, agent, client

**Conversation**:
All turns between one Developer and the Gateway on one task, recognised by its Fingerprint.
_Avoid_: Session, thread, chat

**Turn**:
Everything that follows one new message from the Developer, up to the reply that hands control back to the Developer. One Turn usually spans many requests from the coding agent.
_Avoid_: Request, step, round

**Fingerprint**:
The identity of a Conversation, derived from its first user messages, because the coding agent sends no conversation id.
_Avoid_: Session id, conversation id

**Transcript**:
The list of messages of a Conversation as handed to the Decision.
_Avoid_: History, log, context

### Deciding and intervening

**Decision**:
The verdict, made for every request before it goes upstream, on whether a Rule is broken and which Intervention follows.
_Avoid_: Observer, classification, check

**Intervention**:
What the Gateway does when a Rule is broken: a Flag, a Proposal or a Block.
_Avoid_: Action, enforcement

**Flag**:
A visible marker on a Conversation that a Rule is currently broken, without any effect on the Conversation itself. A Flag disappears once the Rule is no longer broken.
_Avoid_: Warning, alert, violation

**Proposal**:
A suggested next step the Gateway adds to an assistant reply, which the Developer accepts, declines or answers otherwise. Shown at most once per Turn. A declined Proposal returns in the next Turn as long as its Rule stays broken.
_Avoid_: Injection, suggestion, nudge

**Block**:
A refusal by the Gateway to pass a request upstream, answered with an explanation in place of the model's reply. Can happen at any request, and only where the Rule explicitly allows it.
_Avoid_: Deny, reject, stop
