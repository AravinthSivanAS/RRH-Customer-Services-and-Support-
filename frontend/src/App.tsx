import { Fragment, useCallback, useEffect, useReducer, useState } from "react";
import type { Dispatch, SetStateAction } from "react";
import {
  api,
  ApiError,
  type CustomerPreferences,
  type CustomerProfile,
  type InventoryResponse,
  type NotificationResponse,
  type OrderSnapshot,
  type SubstitutionCase,
  type TimelineEntry,
} from "./api";
import Logo from "./Logo";

const CUSTOMER_ID = "CUST-1001";
// Used both to render the scripted delivery-feedback prompt bubble and to
// seed the feedback classifier's context for the customer's first reply to
// it - that prompt is a scripted message, not part of the free-text exchange
// history, so without this the classifier has no idea what a bare "no" is
// even answering.
const DELIVERED_FEEDBACK_PROMPT_FALLBACK = "Was today’s swap right for you?";
const ORDER_ID = "ORD-2026-10001";
const STORE_ID = "STORE-LEEDS-CENTRAL";
const ORIGINAL_SKU = "SKU-MEADOW-LF-1L";

type Scenario = "available" | "none";
type Stage =
  | "welcome"
  | "basket"
  | "order-confirmation"
  | "lock"
  | "chat"
  | "confirmation"
  | "delivery-notification"
  | "tracking"
  | "delivered-notification"
  | "delivered"
  | "preferences"
  | "assistant";
type Mutation = "scenario" | "short-pick" | "response" | "preference" | "restart" | null;
type Speaker = "maya" | "yacy" | "marcus" | "system";

interface DashboardData {
  customer: CustomerProfile;
  preferences: CustomerPreferences;
  inventory: InventoryResponse;
  order: OrderSnapshot;
}

interface ChatMessage {
  id: number;
  speaker: Speaker;
  text: string;
  time: string;
}

interface ComposerExchange {
  text: string;
  time: string;
  reply?: string;
  /** Conversation so far, frozen at the moment this message was sent - never
   * recomputed later, so an earlier bubble's LLM request never changes shape
   * just because a newer message arrived elsewhere in the chat. */
  history: Array<{ speaker: "maya" | "yacy"; text: string }>;
  /** Which generated-message kind/context/fallback this exchange's reply should
   * use - decided deterministically in code (see planFeedbackReply) instead of
   * left for the LLM to infer from raw history, so the conversation's branching
   * logic is reliable; only the actual wording stays LLM-generated. Only used
   * for feedbackExchanges - orderExchanges/chatExchanges use a fixed kind. */
  replyKind?: string;
  replyContext?: Record<string, unknown>;
  replyFallback?: string;
  /** When true, this exchange's reply comes from the open-ended, memory-aware
   * assistant chat instead of a fixed kind - see FeedbackReplyPlan.useAssistant. */
  useAssistant?: boolean;
}

type FeedbackArea = "products" | "delivery";

interface FeedbackFlowState {
  stage: "awaiting-initial" | "awaiting-area" | "awaiting-which-first" | "awaiting-detail" | "done";
  pendingAreas: FeedbackArea[];
  currentArea: FeedbackArea | null;
  details: Partial<Record<FeedbackArea, string>>;
}

const initialFeedbackFlow: FeedbackFlowState = {
  stage: "awaiting-initial",
  pendingAreas: [],
  currentArea: null,
  details: {},
};

interface JourneyState extends Partial<DashboardData> {
  scenario: Scenario;
  stage: Stage;
  history: Stage[];
  caseDetails: SubstitutionCase | null;
  notification: NotificationResponse | null;
  timeline: TimelineEntry[];
  messages: ChatMessage[];
  loading: boolean;
  mutation: Mutation;
  error: { message: string; status: number } | null;
  preferenceSaved: boolean;
  onboardingComplete: boolean;
}

type Action =
  | { type: "loaded"; data: DashboardData }
  | { type: "scenario-ready"; scenario: Scenario; data: DashboardData }
  | { type: "load-error"; error: { message: string; status: number } }
  | { type: "set-mutation"; mutation: Mutation }
  | { type: "set-error"; error: { message: string; status: number } }
  | { type: "navigate"; stage: Stage }
  | { type: "back" }
  | { type: "short-pick-complete"; data: CaseData }
  | { type: "case-refreshed"; data: CaseData }
  | { type: "response-complete"; data: CaseData }
  | { type: "preference-complete"; preferences: CustomerPreferences; continueToBasket: boolean }
  | { type: "add-message"; message: ChatMessage };

interface CaseData {
  caseDetails: SubstitutionCase;
  notification: NotificationResponse;
  order: OrderSnapshot;
  timeline: TimelineEntry[];
  connectingMessage: string;
  marcusGreeting: string;
}

const initialState: JourneyState = {
  scenario: "available",
  stage: "welcome",
  history: [],
  caseDetails: null,
  notification: null,
  timeline: [],
  messages: [],
  loading: true,
  mutation: null,
  error: null,
  preferenceSaved: false,
  onboardingComplete: false,
};

function reducer(state: JourneyState, action: Action): JourneyState {
  switch (action.type) {
    case "loaded":
      return { ...state, ...action.data, loading: false, error: null };
    case "scenario-ready":
      return { ...initialState, ...action.data, scenario: action.scenario, loading: false };
    case "load-error":
      return { ...state, loading: false, error: action.error };
    case "set-mutation":
      return { ...state, mutation: action.mutation, error: null };
    case "set-error":
      return { ...state, mutation: null, error: action.error };
    case "navigate":
      return {
        ...state,
        stage: action.stage,
        history: [...state.history, state.stage],
        error: null,
      };
    case "back": {
      const previous = state.history[state.history.length - 1];
      if (!previous) return state;
      return { ...state, stage: previous, history: state.history.slice(0, -1) };
    }
    case "short-pick-complete":
      return {
        ...state,
        caseDetails: action.data.caseDetails,
        notification: action.data.notification,
        order: action.data.order,
        timeline: action.data.timeline,
        stage: "chat",
        history: [...state.history, "lock"],
        messages: action.data.caseDetails.outcome === "HUMAN_ASSISTANCE_REQUIRED"
          ? [
              {
                id: 1,
                speaker: "yacy",
                text: action.data.notification.message,
                time: "06:46",
              },
              {
                id: 2,
                speaker: "yacy",
                text: action.data.connectingMessage,
                time: "06:46",
              },
              {
                id: 3,
                speaker: "system",
                text: "Marcus joined the conversation",
                time: "06:46",
              },
              {
                id: 4,
                speaker: "marcus",
                text: action.data.marcusGreeting,
                time: "06:47",
              },
            ]
          : [
              {
                id: 1,
                speaker: "yacy",
                text: action.data.notification.message,
                time: "06:46",
              },
            ],
        mutation: null,
        error: null,
      };
    case "case-refreshed":
      return {
        ...state,
        caseDetails: action.data.caseDetails,
        notification: action.data.notification,
        order: action.data.order,
        timeline: action.data.timeline,
        error: null,
      };
    case "response-complete":
      return {
        ...state,
        caseDetails: action.data.caseDetails,
        notification: action.data.notification,
        order: action.data.order,
        timeline: action.data.timeline,
        stage: "confirmation",
        history: [...state.history, state.stage],
        mutation: null,
        error: null,
      };
    case "preference-complete":
      return {
        ...state,
        preferences: action.preferences,
        preferenceSaved: true,
        onboardingComplete: state.onboardingComplete || action.continueToBasket,
        stage: action.continueToBasket ? "basket" : state.stage,
        history: action.continueToBasket ? [...state.history, state.stage] : state.history,
        mutation: null,
      };
    case "add-message":
      return { ...state, messages: [...state.messages, action.message] };
  }
}

async function loadDashboard(): Promise<DashboardData> {
  const [health, customer, preferences, inventory, order] = await Promise.all([
    api.health(),
    api.customer(CUSTOMER_ID),
    api.preferences(CUSTOMER_ID),
    api.inventory(),
    api.order(ORDER_ID),
  ]);
  if (health.status !== "ok") throw new ApiError("Backend health check failed.", 502);
  return { customer, preferences, inventory, order };
}

const FALLBACK_ADVISOR_HANDOFF = "I’m connecting you to Marcus from our care team.";
const FALLBACK_ADVISOR_GREETING =
  "Hi Maya, I can see your order and your Meadow preference. I’ll help you find the right solution.";

async function loadCaseData(notificationId: string): Promise<CaseData> {
  const [caseDetails, notification, order, timeline] = await Promise.all([
    api.substitutionOptions(ORDER_ID),
    api.notification(notificationId),
    api.order(ORDER_ID),
    api.timeline(ORDER_ID),
  ]);

  let connectingMessage = FALLBACK_ADVISOR_HANDOFF;
  let marcusGreeting = FALLBACK_ADVISOR_GREETING;

  if (caseDetails.outcome === "HUMAN_ASSISTANCE_REQUIRED") {
    const context = {
      customer_name: "Maya",
      order_id: ORDER_ID,
      rule_applied: caseDetails.rule_applied,
      routing_reason: caseDetails.human_assistance?.routing_reason,
    };
    [connectingMessage, marcusGreeting] = await Promise.all([
      api
        .generateMessage({ kind: "ADVISOR_HANDOFF_NOTICE", context, fallback: FALLBACK_ADVISOR_HANDOFF })
        .then((result) => result.message)
        .catch(() => FALLBACK_ADVISOR_HANDOFF),
      api
        .generateMessage({ kind: "ADVISOR_GREETING", context, fallback: FALLBACK_ADVISOR_GREETING })
        .then((result) => result.message)
        .catch(() => FALLBACK_ADVISOR_GREETING),
    ]);
  }

  return { caseDetails, notification, order, timeline, connectingMessage, marcusGreeting };
}

function errorDetails(error: unknown) {
  if (error instanceof ApiError) return { message: error.message, status: error.status };
  return {
    message: error instanceof Error ? error.message : "An unexpected error occurred.",
    status: 0,
  };
}

function formatPrice(amount: number, currency = "GBP") {
  return new Intl.NumberFormat("en-GB", { style: "currency", currency }).format(amount);
}

function currentTime() {
  return new Date().toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
}

function formatSlot(slot: string) {
  return slot.replace("-", "–");
}

function formatDeliveryDate(date: string) {
  const parsed = new Date(`${date}T12:00:00`);
  return Number.isNaN(parsed.getTime())
    ? date
    : parsed.toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "short" });
}

function formatTimelineTime(timestamp: string) {
  const date = new Date(timestamp);
  return Number.isNaN(date.getTime())
    ? timestamp
    : date.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
}

function orderTotal(order: OrderSnapshot) {
  return order.order_total ?? order.items.reduce((total, item) => total + item.quantity * item.unit_price, 0);
}

/**
 * Facts the LLM is allowed to state as true - passed as `context` on every
 * conversational follow-up so answers like "list the items" or "what was
 * delivered" reflect the real order instead of a generic deflection.
 */
function buildOrderFacts(state: JourneyState): Record<string, unknown> {
  const order = state.order;
  if (!order) return {};

  const originalItem = order.items.find((item) => item.sku_id === ORIGINAL_SKU);
  const replacementItem = state.notification?.responded_sku_id
    ? order.items.find((item) => item.sku_id === state.notification?.responded_sku_id)
    : undefined;

  return {
    order_id: order.order_id,
    delivery_date: formatDeliveryDate(order.delivery_date),
    delivery_slot: formatSlot(order.delivery_slot),
    delivery_status:
      state.stage === "delivered"
        ? "delivered"
        : state.stage === "tracking" || state.stage === "delivery-notification" || state.stage === "delivered-notification"
          ? "out_for_delivery"
          : "confirmed_not_yet_delivered",
    items: order.items.map((item) => ({ product_name: item.product_name, quantity: item.quantity, status: item.item_status })),
    ...(state.caseDetails && originalItem?.item_status === "OUT_OF_STOCK"
      ? { original_item_out_of_stock: originalItem.product_name }
      : {}),
    substitution_chosen: replacementItem?.product_name,
    resolution_choice: state.notification?.responded_resolution_choice ?? undefined,
  };
}

/**
 * The full back-and-forth so far (this run only, cleared on restart), so a
 * follow-up answer can refer back to something the customer asked earlier
 * instead of treating every message as a stateless one-off.
 */
function buildConversationHistory(
  ...exchangeLists: ComposerExchange[][]
): Array<{ speaker: "maya" | "yacy"; text: string }> {
  const turns: Array<{ speaker: "maya" | "yacy"; text: string }> = [];
  for (const exchanges of exchangeLists) {
    for (const exchange of exchanges) {
      turns.push({ speaker: "maya", text: exchange.text });
      if (exchange.reply) turns.push({ speaker: "yacy", text: exchange.reply });
    }
  }
  return turns;
}

function recordExchangeReply(setExchanges: Dispatch<SetStateAction<ComposerExchange[]>>, index: number, reply: string) {
  setExchanges((prev) => prev.map((exchange, i) => (i === index ? { ...exchange, reply } : exchange)));
}

/** Patches a feedback exchange with its planned reply kind/context once
 * classification has resolved - the exchange itself was already appended
 * (so the customer's own message shows immediately) with no reply kind yet,
 * which renders as a typing indicator until this fills it in. */
function applyFeedbackPlan(setExchanges: Dispatch<SetStateAction<ComposerExchange[]>>, index: number, plan: FeedbackReplyPlan) {
  setExchanges((prev) =>
    prev.map((exchange, i) =>
      i === index
        ? { ...exchange, replyKind: plan.kind, replyContext: plan.context, replyFallback: plan.fallback, useAssistant: plan.useAssistant }
        : exchange,
    ),
  );
}

/**
 * Which stages of the feedback flow need to know the sentiment/area of the
 * customer's reply before deciding what to do next - every other stage
 * (free-text detail capture, or once the flow is done) doesn't need this
 * classification at all.
 */
function feedbackStageNeedsClassification(stage: FeedbackFlowState["stage"]): boolean {
  return stage === "awaiting-initial" || stage === "awaiting-area" || stage === "awaiting-which-first";
}

interface FeedbackClassification {
  area: FeedbackArea | "both" | null;
  sentiment: "negative" | "positive" | "neutral";
}

/**
 * Judging whether a reply is actually a complaint and which area it's about
 * needs real language understanding - a keyword scan reads "sorry i typed
 * wrong, the delivery was great" as a delivery complaint just because it
 * contains the word "delivery". So this asks the backend's LLM-backed
 * classifier (which judges real meaning, including corrections) instead of
 * pattern-matching locally. The FSM branching itself (what to do with that
 * classification) stays deterministic code in planFeedbackReply below - that
 * needs to be reliable every time, whereas free-form LLM-driven branching
 * kept causing loops and repeated questions.
 */
async function classifyFeedback(text: string, history: Array<{ speaker: "maya" | "yacy"; text: string }>): Promise<FeedbackClassification> {
  try {
    const result = await api.classifyFeedback({ text, history });
    return { area: result.area, sentiment: result.sentiment };
  } catch (error) {
    // eslint-disable-next-line no-console
    console.warn("[yacy-llm] feedback classification failed; treating as neutral/unclear", error);
    return { area: null, sentiment: "neutral" };
  }
}

interface FeedbackReplyPlan {
  /** When true, this turn is routed to the open-ended assistant chat instead
   * of a fixed kind - used once the structured feedback flow has concluded
   * and the customer is just chatting, so they can ask anything naturally. */
  useAssistant?: boolean;
  kind: string;
  context: Record<string, unknown>;
  fallback: string;
  nextFlow: FeedbackFlowState;
}

function planFeedbackReply(
  text: string,
  classification: FeedbackClassification,
  flow: FeedbackFlowState,
  orderFacts: Record<string, unknown>,
): FeedbackReplyPlan {
  switch (flow.stage) {
    case "awaiting-initial": {
      if (classification.sentiment === "positive") {
        return {
          kind: "FEEDBACK_THANKS_POSITIVE",
          context: { customer_feedback: text, ...orderFacts },
          fallback: "Thank you for your feedback, Maya - glad everything was just as you wanted!",
          nextFlow: { ...flow, stage: "done" },
        };
      }
      // An ambiguous/unclear reply here must NOT be silently treated as "all
      // good" and end the conversation - that's exactly how a misread "no"
      // ends up getting a cheerful "glad everything went smoothly" reply.
      // Ask plainly instead, and stay on this same stage to re-classify
      // whatever the customer says next.
      if (classification.sentiment === "neutral") {
        return {
          kind: "FEEDBACK_ASK_CLARIFY",
          context: { customer_feedback: text, ...orderFacts },
          fallback: "Just to check - was everything with your order okay, or was there an issue?",
          nextFlow: { ...flow, stage: "awaiting-initial" },
        };
      }
      return {
        kind: "FEEDBACK_ASK_AREA",
        context: { customer_feedback: text, ...orderFacts },
        fallback: "Did the issue concern the products, the delivery, or both?",
        nextFlow: { ...flow, stage: "awaiting-area" },
      };
    }
    case "awaiting-area": {
      // The customer may use this reply to retract/clarify their complaint
      // (e.g. "sorry i typed wrong, the delivery was great") - a topic word
      // like "delivery" appearing doesn't mean it's still a complaint about
      // delivery, so sentiment is checked before area.
      if (classification.sentiment === "positive") {
        return {
          kind: "FEEDBACK_THANKS_POSITIVE",
          context: { customer_feedback: text, ...orderFacts },
          fallback: "Thank you for clarifying, Maya - glad to hear that!",
          nextFlow: { ...flow, stage: "done" },
        };
      }
      if (classification.sentiment === "neutral") {
        return {
          kind: "FEEDBACK_ASK_CLARIFY",
          context: { customer_feedback: text, ...orderFacts },
          fallback: "Just to check - did the issue concern the products, the delivery, or both?",
          nextFlow: { ...flow, stage: "awaiting-area" },
        };
      }
      if (classification.area === "both") {
        return {
          kind: "FEEDBACK_ASK_WHICH_FIRST",
          context: { customer_feedback: text, ...orderFacts },
          fallback: "Would you like to detail the products or the delivery first?",
          nextFlow: { ...flow, stage: "awaiting-which-first", pendingAreas: ["products", "delivery"] },
        };
      }
      const singleArea: FeedbackArea = classification.area === "products" || classification.area === "delivery" ? classification.area : "products";
      return {
        kind: "FEEDBACK_ASK_AREA_DETAIL",
        context: { customer_feedback: text, area: singleArea, ...orderFacts },
        fallback: `Could you share more about what went wrong with the ${singleArea}?`,
        nextFlow: { ...flow, stage: "awaiting-detail", currentArea: singleArea, pendingAreas: [] },
      };
    }
    case "awaiting-which-first": {
      if (classification.sentiment === "positive") {
        return {
          kind: "FEEDBACK_THANKS_POSITIVE",
          context: { customer_feedback: text, ...orderFacts },
          fallback: "Thank you for clarifying, Maya - glad to hear that!",
          nextFlow: { ...flow, stage: "done" },
        };
      }
      if (classification.sentiment === "neutral") {
        return {
          kind: "FEEDBACK_ASK_CLARIFY",
          context: { customer_feedback: text, ...orderFacts },
          fallback: "Just to check - would you like to detail the products or the delivery first?",
          nextFlow: { ...flow, stage: "awaiting-which-first" },
        };
      }
      const picked = classification.area;
      const area: FeedbackArea = picked === "products" || picked === "delivery" ? picked : flow.pendingAreas[0];
      const remaining = flow.pendingAreas.filter((candidate) => candidate !== area);
      return {
        kind: "FEEDBACK_ASK_AREA_DETAIL",
        context: { customer_feedback: text, area, ...orderFacts },
        fallback: `Could you share more about what went wrong with the ${area}?`,
        nextFlow: { ...flow, stage: "awaiting-detail", currentArea: area, pendingAreas: remaining },
      };
    }
    case "awaiting-detail": {
      const area: FeedbackArea = flow.currentArea ?? "products";
      const details = { ...flow.details, [area]: text };
      if (flow.pendingAreas.length > 0) {
        const nextArea = flow.pendingAreas[0];
        return {
          kind: "FEEDBACK_ASK_NEXT_AREA_DETAIL",
          context: { customer_feedback: text, acknowledged_area: area, next_area: nextArea, ...orderFacts },
          fallback: `Thanks for sharing that about the ${area}. And what about the ${nextArea} - what went wrong there?`,
          nextFlow: { ...flow, stage: "awaiting-detail", currentArea: nextArea, pendingAreas: flow.pendingAreas.slice(1), details },
        };
      }
      return {
        kind: "FEEDBACK_FINAL_ACK",
        context: { customer_feedback: text, area, details, ...orderFacts },
        fallback: "Thank you for the detailed feedback, Maya - we'll make sure this is addressed for your next order.",
        nextFlow: { ...flow, stage: "done", details },
      };
    }
    case "done":
    default:
      return {
        useAssistant: true,
        kind: "ASSISTANT_CHAT",
        context: {},
        fallback: "Thanks for your message! Let us know if there's anything else we can help with.",
        nextFlow: flow,
      };
  }
}

function makeMessage(speaker: Speaker, text: string): ChatMessage {
  return { id: Date.now() + Math.random(), speaker, text, time: currentTime() };
}

/**
 * Fetches LLM-generated customer-facing wording for a given message `kind`.
 * Reports `isPending` while the request for the *current* key is in flight,
 * so callers can show a typing indicator instead of flashing the fallback
 * text and then swapping it out. Always resolves to `fallback` if the
 * backend/LLM call fails, so the demo narrative never gets stuck.
 */
/**
 * Shared by every LLM-backed bubble (scripted/kind-based and open-ended
 * assistant chat alike): fetch once per distinct `key`, and once any text has
 * ever resolved for this bubble, keep showing it even if `key` later changes
 * under it - never blank back out to the fallback/typing state. A newer
 * result (if the key does change) replaces it quietly once it arrives.
 */
// A cached/fast response can come back in well under a human's reaction
// time, which made the "typing…" indicator flash invisibly and made a run of
// bubbles look like they all popped in together instead of one by one. This
// guarantees every bubble's typing indicator is on screen for at least this
// long, regardless of how fast the underlying call actually resolves.
const MIN_TYPING_INDICATOR_MS = 700;

function useAsyncReply(
  key: string,
  fetchReply: () => Promise<{ text: string; source: string }>,
  fallback: string,
  skip: boolean,
  logLabel: string,
): { text: string; isPending: boolean } {
  const [resolved, setResolved] = useState<{ key: string; text: string } | null>(null);

  useEffect(() => {
    if (skip) return;
    let cancelled = false;
    const startedAt = Date.now();

    const reveal = (text: string) => {
      if (cancelled) return;
      const remaining = MIN_TYPING_INDICATOR_MS - (Date.now() - startedAt);
      if (remaining > 0) {
        setTimeout(() => {
          if (!cancelled) setResolved({ key, text });
        }, remaining);
      } else {
        setResolved({ key, text });
      }
    };

    fetchReply()
      .then((result) => {
        // eslint-disable-next-line no-console
        console.info(`[yacy-llm] ${logLabel} -> source=${result.source}`, result.text);
        reveal(result.text);
      })
      .catch((error) => {
        // eslint-disable-next-line no-console
        console.warn(`[yacy-llm] ${logLabel} -> request failed, using fallback`, error);
        reveal(fallback);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, skip]);

  return {
    text: resolved ? resolved.text : fallback,
    isPending: resolved === null && !skip,
  };
}

function useGeneratedMessage(
  kind: string,
  context: Record<string, unknown>,
  fallback: string,
  skip = false,
): { text: string; isPending: boolean } {
  const key = `${kind}::${JSON.stringify(context)}`;
  return useAsyncReply(
    key,
    () => api.generateMessage({ kind, context, fallback }).then((result) => ({ text: result.message, source: result.source })),
    fallback,
    skip,
    kind,
  );
}

/** Open-ended, memory-aware assistant chat - the backend owns conversation
 * history per customer, so the key only needs the message itself to stay
 * stable forever once sent (no client-side history snapshot required). */
function useAssistantReply(
  customerId: string,
  orderId: string | null | undefined,
  message: string,
  fallback: string,
  narrativeStage: "order_placed" | "current" | "delivered" = "current",
  skip = false,
): { text: string; isPending: boolean } {
  const key = `assistant::${customerId}::${orderId ?? ""}::${narrativeStage}::${message}`;
  return useAsyncReply(
    key,
    () =>
      api
        .assistantChat({ customer_id: customerId, order_id: orderId ?? null, message, narrative_stage: narrativeStage })
        .then((result) => ({ text: result.reply, source: result.source })),
    fallback,
    skip,
    "assistant-chat",
  );
}

export default function App() {
  const [state, dispatch] = useReducer(reducer, initialState);
  const [reply, setReply] = useState("");
  const [orderExchanges, setOrderExchanges] = useState<ComposerExchange[]>([]);
  const [chatExchanges, setChatExchanges] = useState<ComposerExchange[]>([]);
  const [feedbackExchanges, setFeedbackExchanges] = useState<ComposerExchange[]>([]);
  const [feedbackFlow, setFeedbackFlow] = useState<FeedbackFlowState>(initialFeedbackFlow);
  const resolved = state.caseDetails?.resolved === true;
  const showMarcus = state.caseDetails?.outcome === "HUMAN_ASSISTANCE_REQUIRED";
  const originalItem = state.order?.items.find((item) => item.sku_id === ORIGINAL_SKU);

  const refreshDashboard = useCallback(async () => {
    try {
      dispatch({ type: "loaded", data: await loadDashboard() });
    } catch (error) {
      dispatch({ type: "load-error", error: errorDetails(error) });
    }
  }, []);

  useEffect(() => {
    void refreshDashboard();
  }, [refreshDashboard]);

  async function switchScenario(scenario: Scenario) {
    if (scenario === state.scenario || state.mutation) return;
    dispatch({ type: "set-mutation", mutation: "scenario" });
    try {
      await api.resetDemo();
      if (scenario === "none") await api.prepareNoMeadowScenario();
      dispatch({ type: "scenario-ready", scenario, data: await loadDashboard() });
      setReply("");
      setOrderExchanges([]);
      setChatExchanges([]);
      setFeedbackExchanges([]);
      setFeedbackFlow(initialFeedbackFlow);
    } catch (error) {
      dispatch({ type: "set-error", error: errorDetails(error) });
    }
  }

  async function restartScenario() {
    if (state.mutation) return;
    dispatch({ type: "set-mutation", mutation: "restart" });
    try {
      await api.resetDemo();
      if (state.scenario === "none") await api.prepareNoMeadowScenario();
      dispatch({ type: "scenario-ready", scenario: state.scenario, data: await loadDashboard() });
      setReply("");
      setOrderExchanges([]);
      setChatExchanges([]);
      setFeedbackExchanges([]);
      setFeedbackFlow(initialFeedbackFlow);
    } catch (error) {
      dispatch({ type: "set-error", error: errorDetails(error) });
    }
  }

  async function openNotification() {
    if (state.mutation || !state.order || state.caseDetails) return;
    const milk = state.order.items.find((item) => item.sku_id === ORIGINAL_SKU);
    if (!milk) {
      dispatch({ type: "set-error", error: { message: "The Meadow item is missing from the active order.", status: 502 } });
      return;
    }
    dispatch({ type: "set-mutation", mutation: "short-pick" });
    try {
      const stock = await api.productStock(ORIGINAL_SKU, STORE_ID);
      const result = await api.shortPick({
        event_type: "SHORT_PICK_RECORDED",
        event_time: new Date().toISOString(),
        order_id: ORDER_ID,
        customer_id: CUSTOMER_ID,
        store_id: STORE_ID,
        sku_id: ORIGINAL_SKU,
        requested_quantity: milk.quantity,
        available_quantity: stock.available_quantity,
        picker_id: "PICKER-204",
      });
      const data = await loadCaseData(result.notification.notification_id);
      dispatch({ type: "short-pick-complete", data });
    } catch (error) {
      dispatch({ type: "set-error", error: errorDetails(error) });
    }
  }

  async function chooseOption(skuId: string) {
    if (!state.notification || !state.caseDetails || state.mutation || resolved) return;
    dispatch({ type: "set-mutation", mutation: "response" });
    try {
      const response = await api.respondToNotification(state.notification.notification_id, {
        selected_sku_id: skuId,
      });
      const data = await loadCaseData(response.notification_id);
      dispatch({ type: "response-complete", data });
    } catch (error) {
      dispatch({ type: "set-error", error: errorDetails(error) });
    }
  }

  async function chooseResolution(choice: string) {
    if (!state.notification || !state.caseDetails || state.mutation || resolved) return;
    dispatch({ type: "set-mutation", mutation: "response" });
    try {
      const response = await api.respondToNotification(state.notification.notification_id, {
        resolution_choice: choice,
      });
      const data = await loadCaseData(response.notification_id);
      dispatch({ type: "response-complete", data });
    } catch (error) {
      dispatch({ type: "set-error", error: errorDetails(error) });
    }
  }

  async function savePreference() {
    const preference = state.preferences?.product_preferences[0];
    if (!preference || state.mutation) return;
    const continueToBasket = !state.onboardingComplete;
    dispatch({ type: "set-mutation", mutation: "preference" });
    try {
      const preferences = await api.savePreference(CUSTOMER_ID, {
        product_category: preference.product_category,
        preferred_brand: preference.preferred_brand,
        preferred_sku_id: preference.preferred_sku_id,
        substitution_rule: preference.substitution_rule,
        price_increase_limit_percentage: preference.price_increase_limit_percentage,
        preferred_product_name: preference.preferred_product_name,
        notes: preference.notes,
      });
      dispatch({ type: "preference-complete", preferences, continueToBasket });
      if (!continueToBasket) {
        dispatch({ type: "add-message", message: makeMessage("yacy", "Your Meadow preference is saved for future orders.") });
      }
    } catch (error) {
      dispatch({ type: "set-error", error: errorDetails(error) });
    }
  }

  function sendMarcusReply() {
    const message = reply.trim();
    if (!message || resolved) return;
    dispatch({ type: "add-message", message: makeMessage("marcus", message) });
    setReply("");
  }

  async function retry() {
    if (state.caseDetails && state.notification) {
      try {
        dispatch({ type: "case-refreshed", data: await loadCaseData(state.notification.notification_id) });
      } catch (error) {
        dispatch({ type: "set-error", error: errorDetails(error) });
      }
      return;
    }
    await refreshDashboard();
  }

  function navigate(stage: Stage) {
    dispatch({ type: "navigate", stage });
  }

  if (state.loading || !state.customer || !state.preferences || !state.inventory || !state.order) {
    return (
      <main className="loading-screen">
        <span className="spinner" />
        {state.error ? (
          <div className="load-error" role="alert">
            <strong>{state.error.status === 0 ? "Backend unavailable" : "Could not load demo data"}</strong>
            <span>{state.error.message}</span>
            <button onClick={() => void refreshDashboard()}>Retry</button>
          </div>
        ) : <span>Loading…</span>}
      </main>
    );
  }

  return (
    <main className="demo-app">
      <nav className="demo-controls" aria-label="Demo controls">
        <div className="scenario-switch">
          <button className={state.scenario === "available" ? "active" : ""} onClick={() => void switchScenario("available")} disabled={Boolean(state.mutation)}>Happy path</button>
          <button className={state.scenario === "none" ? "active" : ""} onClick={() => void switchScenario("none")} disabled={Boolean(state.mutation)}>Exception path</button>
        </div>
        <button className="restart-control" onClick={() => void restartScenario()} disabled={Boolean(state.mutation)}>
          {state.mutation === "restart" ? "Restarting…" : "Restart"}
        </button>
      </nav>

      {state.error && (
        <div className="error-banner" role="alert">
          <span>
            {state.error.status === 0
              ? "Backend unavailable"
              : state.error.status === 409
                ? state.error.message.toLowerCase().includes("stale inventory")
                  ? "Stock changed before your choice. Select another option or restart."
                  : "This short pick was already submitted. Restart the scenario to continue."
                : state.error.message}
          </span>
          <button onClick={() => void retry()} disabled={Boolean(state.mutation)}>Retry safe reads</button>
        </div>
      )}

      {state.mutation === "scenario" && <span className="sr-only" role="status">Preparing scenario</span>}

      <section className={`products ${showMarcus ? "products--with-marcus" : "products--phone-only"}`} aria-label="Customer and service experience">
        <section className="maya-column">
          <h1 className="panel-label">Maya</h1>
          <div className="phone-frame">
            <div className="phone-screen">
              <div className="phone-notch" />
              <div className="phone-status"><strong>{state.stage === "tracking" ? "17:30" : state.stage === "delivered" ? "18:40" : state.stage === "basket" ? "19:30" : state.stage === "order-confirmation" ? "19:31" : state.stage === "welcome" ? "19:11" : state.stage === "preferences" ? "19:12" : "06:46"}</strong><span>▮▮▮ ◉ ▱</span></div>

              {state.stage === "welcome" && (
                <div className="welcome-screen">
                  <div className="welcome-date">Tuesday</div>
                  <div className="welcome-time">19:11</div>
                  <button className="push-notification welcome-notification" onClick={() => navigate("preferences")}>
                    <Logo compact />
                    <span><strong>Yacy · Your basket is ready</strong><small>Take a look at your basket and choose what’s right for you.</small></span>
                    <time>now</time>
                  </button>
                </div>
              )}

              {state.stage === "lock" && (
                <div className="lock-screen">
                  <div className="lock-date">Tuesday</div>
                  <div className="lock-time">06:46</div>
                  <button className="push-notification" onClick={() => void openNotification()} disabled={Boolean(state.mutation)}>
                    <Logo compact />
                    <span><strong>WhatsApp · Yacy</strong><small>{state.mutation === "short-pick" ? "Opening your message…" : state.scenario === "available" ? "Your Meadow milk is out of stock today. Two Meadow options, same price…" : "Your Meadow milk is out of stock today. We’re finding the right solution…"}</small></span>
                    <time>now</time>
                  </button>
                  {state.error?.status === 409 && <button className="restart-inline" onClick={() => void restartScenario()}>Restart scenario</button>}
                </div>
              )}

              {state.stage === "delivered-notification" && (
                <div className="lock-screen">
                  <div className="lock-date">Tuesday</div>
                  <div className="lock-time">18:42</div>
                  <button className="push-notification" onClick={() => navigate("delivered")}>
                    <Logo compact />
                    <span><strong>WhatsApp · Yacy</strong><small>Your order has been delivered. Tap to see details.</small></span>
                    <time>now</time>
                  </button>
                </div>
              )}

              {state.stage !== "lock" && state.stage !== "welcome" && state.stage !== "delivered-notification" && (
                <PhoneExperience
                  state={state}
                  originalPrice={originalItem?.unit_price ?? 3.49}
                  onBack={() => dispatch({ type: "back" })}
                  onChooseOption={(sku) => void chooseOption(sku)}
                  onChooseResolution={(choice) => void chooseResolution(choice)}
                  onNavigate={navigate}
                  onSavePreference={() => void savePreference()}
                  orderExchanges={orderExchanges}
                  setOrderExchanges={setOrderExchanges}
                  chatExchanges={chatExchanges}
                  setChatExchanges={setChatExchanges}
                  feedbackExchanges={feedbackExchanges}
                  setFeedbackExchanges={setFeedbackExchanges}
                  feedbackFlow={feedbackFlow}
                  setFeedbackFlow={setFeedbackFlow}
                />
              )}
            </div>
            {state.stage === "order-confirmation" && orderExchanges.length > 0 && (
              <button className="continue-outside-phone" aria-label="Continue" onClick={() => navigate("lock")}>
                →
              </button>
            )}
            {state.stage === "confirmation" && chatExchanges.length > 0 && (
              <button className="continue-outside-phone" aria-label="Continue" onClick={() => navigate("delivery-notification")}>
                →
              </button>
            )}
          </div>
        </section>

        {showMarcus && (
          <>
            <div className="sync-mark" aria-hidden="true">↔</div>
            <section className="marcus-column" aria-label="Marcus service console">
              <h2 className="panel-label">Marcus</h2>
              <ServiceConsole
                state={state}
                onChooseResolution={(choice) => void chooseResolution(choice)}
                reply={reply}
                setReply={setReply}
                onSendReply={sendMarcusReply}
                orderExchanges={orderExchanges}
                chatExchanges={chatExchanges}
                feedbackExchanges={feedbackExchanges}
              />
            </section>
          </>
        )}
      </section>
    </main>
  );
}

function PhoneExperience({
  state,
  originalPrice,
  onBack,
  onChooseOption,
  onChooseResolution,
  onNavigate,
  onSavePreference,
  orderExchanges,
  setOrderExchanges,
  chatExchanges,
  setChatExchanges,
  feedbackExchanges,
  setFeedbackExchanges,
  feedbackFlow,
  setFeedbackFlow,
}: {
  state: JourneyState;
  originalPrice: number;
  onBack: () => void;
  onChooseOption: (sku: string) => void;
  onChooseResolution: (choice: string) => void;
  onNavigate: (stage: Stage) => void;
  onSavePreference: () => void;
  orderExchanges: ComposerExchange[];
  setOrderExchanges: Dispatch<SetStateAction<ComposerExchange[]>>;
  chatExchanges: ComposerExchange[];
  setChatExchanges: Dispatch<SetStateAction<ComposerExchange[]>>;
  feedbackExchanges: ComposerExchange[];
  setFeedbackExchanges: Dispatch<SetStateAction<ComposerExchange[]>>;
  feedbackFlow: FeedbackFlowState;
  setFeedbackFlow: Dispatch<SetStateAction<FeedbackFlowState>>;
}) {
  useEffect(() => {
    if (state.stage !== "tracking") return;
    const timer = setTimeout(() => onNavigate("delivered-notification"), 3000);
    return () => clearTimeout(timer);
  }, [state.stage, onNavigate]);

  if (state.stage === "basket" && state.order) {
    const order = state.order;
    return (
      <div className="phone-app basket-app">
        <div className="phone-appbar"><Logo compact /><strong>Your basket</strong><small className="basket-count">{order.items.reduce((count, item) => count + item.quantity, 0)} items</small></div>
        <div className="basket-content">
          {order.items.map((item) => {
            const isPreferredItem = item.sku_id === ORIGINAL_SKU;
            return (
              <article className={`basket-item${isPreferredItem ? " basket-item--preferred" : ""}`} key={item.sku_id}>
                <span className="item-icon">{isPreferredItem ? "🥛" : "📦"}</span>
                <div className="basket-item-details">
                  <strong>{item.product_name}</strong>
                  <small>{isPreferredItem ? `${state.preferences?.product_preferences[0]?.preferred_brand} · preferred item` : `Quantity ${item.quantity}`}</small>
                  {isPreferredItem && <em>Substitution: same brand, or ask me</em>}
                </div>
                <b>{formatPrice(item.unit_price * item.quantity, order.currency)}</b>
              </article>
            );
          })}
          <div className="basket-toggle"><span>Updates on WhatsApp</span><i className={state.customer?.contact_details.whatsapp_opt_in ? "toggle-on" : ""} /></div>
          <div className="basket-slot">🚚　{formatDeliveryDate(order.delivery_date)} · {formatSlot(order.delivery_slot)} · Home delivery</div>
        </div>
        <div className="basket-checkout">
          <button className="button primary-button" onClick={() => onNavigate("order-confirmation")}>
            Checkout · {formatPrice(orderTotal(state.order), state.order.currency)}
          </button>
        </div>
      </div>
    );
  }

  if (state.stage === "tracking" || state.stage === "delivery-notification") {
    return (
      <div className="phone-app tracking-app">
        <div className="phone-appbar"><button onClick={onBack} aria-label="Back">‹</button><Logo compact /><strong>{state.stage === "tracking" ? "Track your order" : "Yacy"}</strong></div>
        {state.stage === "delivery-notification" ? (
          <div className="notification-screen">
            <button className="push-notification push-notification--light" onClick={() => onNavigate("tracking")}>
              <Logo compact /><span><strong>Yacy</strong><small>Your order is out for delivery — arriving about 18:40. Track live.</small></span><time>17:30</time>
            </button>
          </div>
        ) : (
          <div className="tracking-body">
            <div className="fictional-map">
              <svg className="map-canvas" viewBox="0 0 260 190" preserveAspectRatio="none" aria-hidden="true">
                <rect x="0" y="0" width="260" height="190" fill="#eef1ea" />
                <rect className="map-block" x="10" y="12" width="50" height="34" />
                <rect className="map-block" x="70" y="100" width="44" height="30" />
                <rect className="map-block" x="150" y="118" width="40" height="26" />
                <rect className="map-block" x="190" y="20" width="36" height="24" />
                <rect className="map-block map-block--park" x="112" y="30" width="46" height="34" rx="4" />
                <path className="map-road-line map-road-line--minor" d="M-10 64 L270 48" />
                <path className="map-road-line" d="M34 -10 L10 200" />
                <path className="map-road-line map-road-line--route-road" d="M-10 150 L270 150" />
                <path className="map-road-line map-road-line--route-road" d="M217 -10 L217 200" />
                <text className="map-street-label" x="110" y="144">Leeds Rd</text>
                <text className="map-street-label" x="150" y="40">Mill Ln</text>
                {/* Follows the roads above (right along Leeds Rd, then up the cross
                    street to Home) instead of cutting a straight line through the
                    blocks - dash pattern + round caps renders as discrete dots. */}
                <path
                  id="delivery-route"
                  d="M43 150 L217 150 L217 36"
                  fill="none"
                  stroke="#18864b"
                  strokeWidth="6"
                  strokeLinecap="round"
                  strokeDasharray="0.1 12"
                />
                <g className="map-location map-location--store" transform="translate(24 136)">
                  <path d="M2 8h19v13H2zM0 8l3-6h17l3 6v3H0z" fill="#fff" stroke="#53675a" strokeWidth="1.5" strokeLinejoin="round" />
                  <path d="M7 14h5v7H7zM15 13h3v3h-3z" fill="#c9dfd0" stroke="#53675a" strokeWidth="1" />
                  <text x="0" y="32">STORE</text>
                </g>
                <g className="map-location map-location--home" transform="translate(215 15)">
                  <path d="M1 10 10 2l9 8v11h-7v-7H8v7H1z" fill="#fff" stroke="#53675a" strokeWidth="1.5" strokeLinejoin="round" />
                  <text x="-1" y="31">HOME</text>
                </g>
                <g className="scooter-marker">
                  <animateMotion dur="35s" repeatCount="1" fill="freeze" rotate="auto">
                    <mpath href="#delivery-route" />
                  </animateMotion>
                  <g transform="translate(-10 -9)">
                    <circle cx="5" cy="15" r="3" fill="#fff" stroke="#34443a" strokeWidth="1.5" />
                    <circle cx="17" cy="15" r="3" fill="#fff" stroke="#34443a" strokeWidth="1.5" />
                    <path d="M5 14h7l4-5h3l2 5h-5l-3-3-3 4H8l-2-3H5z" fill="#16864c" stroke="#126b3d" strokeWidth="1" strokeLinejoin="round" />
                    <path d="M15 8h4M16 8l-1-3M14 5h3" fill="none" stroke="#34443a" strokeWidth="1.5" strokeLinecap="round" />
                  </g>
                </g>
              </svg>
              <button className="map-locate-button" aria-label="Center map" type="button">◎</button>
            </div>
            <div className="tracking-summary">
              <strong>Arriving 18:40 · 3 stops away</strong>
              <span>Driver: Sam · Yacy van 12</span>
              {state.order && <p>Delivery slot {formatSlot(state.order.delivery_slot)}</p>}
            </div>
            {state.order?.items.filter((item) => item.item_status === "APPROVED_SUBSTITUTION").map((item) => (
              <div className="tracked-item" key={item.sku_id}><span>🥛</span><div><strong>{item.product_name}</strong><small>Approved substitution</small></div></div>
            ))}
          </div>
        )}
      </div>
    );
  }

  if (state.stage === "preferences") {
    const preference = state.preferences?.product_preferences[0];
    return (
      <div className="phone-app preferences-app">
        <div className="phone-appbar">{state.onboardingComplete && <button onClick={onBack} aria-label="Back">‹</button>}<Logo compact /><strong>My substitution preferences</strong></div>
        <div className="preferences-content">
          <span className="mini-label">BY ITEM</span>
          <div className="preference-tile preference-tile--other">
            <span className="item-icon">📦</span><span><strong>Everything else</strong><small>No saved preference</small></span>
          </div>
          <button className="preference-tile">
            <span className="item-icon">🥛</span><span><strong>Son’s milk</strong><small>{preference?.preferred_product_name}</small><em>Same brand, or ask me</em></span>
          </button>
          <span className="mini-label">HOW WE USE THIS</span>
          <div className="preference-toggle"><span><strong>Check substitutions against these preferences</strong><small>We’ll never swap in a brand you’ve ruled out</small></span><i className={state.preferences?.allow_preference_based_recommendations ? "toggle-on" : ""} /></div>
          <div className="preference-toggle"><span><strong>Message me on WhatsApp about changes</strong></span><i className={state.customer?.contact_details.whatsapp_opt_in ? "toggle-on" : ""} /></div>
          <button className="button primary-button save-preference" onClick={onSavePreference} disabled={Boolean(state.mutation)}>
            {state.mutation === "preference" ? "Saving…" : !state.onboardingComplete ? "Save preferences and continue" : state.preferenceSaved ? "Saved" : "Save preferences"}
          </button>
          {state.preferenceSaved && <span className="saved-chip">✓ Preferences updated</span>}
          {!state.onboardingComplete && <small className="preferences-footnote">Used for your orders and substitutions. Change or remove it any time.</small>}
          {state.onboardingComplete && <button className="text-link" onClick={onBack}>Back to chat</button>}
        </div>
      </div>
    );
  }

  if (state.stage === "assistant") {
    return (
      <div className="phone-app assistant-app">
        <div className="phone-appbar"><button onClick={onBack} aria-label="Back">‹</button><Logo compact /><div><strong>Yacy Assistant</strong><small>Virtual agent · usually replies instantly</small></div></div>
        <div className="assistant-messages">
          <ChatBubble speaker="maya" text="Can you always keep his milk to Meadow?" time="18:50" />
          <GeneratedChatBubble
            speaker="yacy"
            kind="ASSISTANT_PREFERENCE_ACK"
            context={{ preferred_brand: "Meadow", product_category: "Milk" }}
            fallback="Of course, Maya. I’ve saved Meadow only, or ask me for his milk on your profile — it will apply to every order. Shall I do the same for his yoghurt?"
            time="18:50"
          />
          <ChatBubble speaker="maya" text="Yes please" time="18:51" />
          <GeneratedChatBubble
            speaker="yacy"
            kind="ASSISTANT_PREFERENCE_DONE"
            context={{ preferred_brand: "Meadow", items: ["Milk", "yoghurt"] }}
            fallback="Done ✓ Milk and yoghurt."
            time="18:51"
          />
          <span className="saved-chip">✓ Preferences updated</span>
        </div>
        <div className="composer"><input aria-label="Ask about your orders" placeholder="Ask anything about your orders…" /><button aria-label="Send">↑</button></div>
      </div>
    );
  }

  const order = state.order;
  const messages = state.messages;
  const isChatOrConfirmation =
    state.stage === "chat" || state.stage === "confirmation" || state.stage === "delivered";
  const showOptions = isChatOrConfirmation && Boolean(state.caseDetails?.valid_alternatives.length);
  const showResolution = isChatOrConfirmation && Boolean(state.caseDetails?.human_assistance);
  const showConfirmation = state.stage === "confirmation";
  const confirmationUpdatesVisible = state.stage === "confirmation" || state.stage === "delivered";
  const assistance = state.caseDetails?.outcome === "HUMAN_ASSISTANCE_REQUIRED";

  return (
    <div className="phone-app chat-app">
      <PhoneChatHeader onNavigate={onNavigate} onBack={onBack} />
      <div className="chat-history" aria-live="polite">
        <div className="day-stamp">TODAY</div>
        {order && (
          <>
            <GeneratedChatBubble
              speaker="yacy"
              kind="ORDER_CONFIRMATION"
              context={{
                customer_name: state.customer?.customer_name,
                order_id: order.order_id,
                delivery_date: formatDeliveryDate(order.delivery_date),
                delivery_slot: formatSlot(order.delivery_slot),
              }}
              fallback={`Hi ${state.customer?.customer_name} 👋 Your order ${order.order_id} is confirmed for ${formatDeliveryDate(order.delivery_date)}, ${formatSlot(order.delivery_slot)}. We’ll message you here if anything changes.`}
              time="19:31"
            />
            {orderExchanges.map((exchange, index) => (
              <Fragment key={`order-${index}-${exchange.time}`}>
                <ChatBubble speaker="maya" text={exchange.text} time={exchange.time} />
                {exchange.reply ? (
                  <ChatBubble speaker="yacy" text={exchange.reply} time={exchange.time} />
                ) : (
                  <AssistantChatBubble
                    customerId={CUSTOMER_ID}
                    orderId={order.order_id}
                    message={exchange.text}
                    fallback="Thanks for your message! Let us know if there's anything else about your order."
                    time={exchange.time}
                    narrativeStage="order_placed"
                    onResolved={(text) => recordExchangeReply(setOrderExchanges, index, text)}
                  />
                )}
              </Fragment>
            ))}
          </>
        )}
        {messages.map((message) => (
          message.speaker === "system"
            ? <span className="chat-system" key={message.id}>{message.text}</span>
            : <ChatBubble key={message.id} speaker={message.speaker} text={message.text} time={message.time} />
        ))}
        {showOptions && (
          <div className="message-card">
            <p>Here are two Meadow options at the same price:</p>
            {state.caseDetails?.valid_alternatives.map((option, index) => (
              <div className="chat-option" key={option.sku_id}>
                <span className="milk-pack">M<br /><small>LF</small></span>
                <div><b>OPTION {index + 1}</b><strong>{option.product_name}</strong><small>{option.brand} · {formatPrice(option.unit_price)}</small></div>
                <span className="option-check">✓ Same brand</span>
              </div>
            ))}
            <span className="message-time">06:46</span>
          </div>
        )}
        {showOptions && (
          <div className="customer-actions">
            {state.caseDetails?.valid_alternatives.map((option, index) => (
              <button
                key={option.sku_id}
                className={option.sku_id === state.notification?.responded_sku_id ? "chosen" : ""}
                onClick={() => onChooseOption(option.sku_id)}
                disabled={Boolean(state.mutation) || state.caseDetails?.resolved}
              >
                {state.mutation === "response" ? "Updating…" : `Option ${index + 1}`}
              </button>
            ))}
          </div>
        )}
        {showResolution && (
          <div className="resolution-chat-actions">
            <p>Choose how you’d like us to resolve the missing item.</p>
            {state.caseDetails?.human_assistance?.resolution_options.map((option) => (
              <button
                key={option.type}
                className={option.type === state.notification?.responded_resolution_choice ? "chosen" : ""}
                onClick={() => onChooseResolution(option.type)}
                disabled={Boolean(state.mutation) || state.caseDetails?.resolved}
              >
                {option.type === "WALLET_CREDIT" ? "Instant Yacy wallet credit" : "Refund to card · 3–5 days"}
                <strong>{formatPrice(option.amount, option.currency)}</strong>
              </button>
            ))}
          </div>
        )}
        {state.caseDetails?.resolved && state.notification?.responded_sku_id && (
          <ChatBubble
            speaker="maya"
            text={`Option ${
              (state.caseDetails.valid_alternatives.findIndex(
                (option) => option.sku_id === state.notification?.responded_sku_id,
              ) ?? -1) + 1
            }`}
            time={currentTime()}
          />
        )}
        {state.caseDetails?.resolved && state.notification?.responded_resolution_choice && (
          <ChatBubble
            speaker="maya"
            text={
              state.notification.responded_resolution_choice === "WALLET_CREDIT"
                ? "Instant Yacy wallet credit"
                : "Refund to card"
            }
            time={currentTime()}
          />
        )}
        {confirmationUpdatesVisible && (() => {
          const swappedProductName = state.order?.items.find(
            (item) => item.sku_id === state.notification?.responded_sku_id,
          )?.product_name ?? "Your Meadow replacement";

          const items: SequencedBubbleSpec[] = [
            {
              speaker: assistance ? "marcus" : "yacy",
              kind: assistance ? "HUMAN_ASSISTANCE_CASE_UPDATE" : "SUBSTITUTION_CONFIRMATION_UPDATE",
              context: {},
              fallback: assistance
                ? "Your choice is recorded. I’ve updated the case."
                : "Great choice. Updating your order now…",
              time: currentTime(),
            },
          ];

          if (state.notification?.responded_sku_id) {
            items.push({
              speaker: "yacy",
              kind: "SWAP_ADDED_CONFIRMATION",
              context: { product_name: swappedProductName },
              fallback: `${swappedProductName} is added to your order.`,
              time: currentTime(),
            });
          }

          if (state.notification?.responded_resolution_choice) {
            items.push({
              speaker: "yacy",
              kind: "RESOLUTION_CONFIRMATION",
              context: {
                resolution_choice: state.notification.responded_resolution_choice,
                amount: originalPrice,
                currency: "GBP",
              },
              fallback:
                state.notification.responded_resolution_choice === "WALLET_CREDIT"
                  ? `${formatPrice(originalPrice)} added to your Yacy wallet.`
                  : `Your ${formatPrice(originalPrice)} card refund is being processed.`,
              time: currentTime(),
            });
          }

          return <SequentialChatBubbles items={items} />;
        })()}
        {confirmationUpdatesVisible && chatExchanges.map((exchange, index) => (
          <Fragment key={`${index}-${exchange.time}`}>
            <ChatBubble speaker="maya" text={exchange.text} time={exchange.time} />
            {exchange.reply ? (
              <ChatBubble speaker="yacy" text={exchange.reply} time={exchange.time} />
            ) : (
              <AssistantChatBubble
                customerId={CUSTOMER_ID}
                orderId={state.order?.order_id}
                message={exchange.text}
                fallback="Thanks for your message! Let us know if there's anything else we can help with."
                time={exchange.time}
                onResolved={(text) => recordExchangeReply(setChatExchanges, index, text)}
              />
            )}
          </Fragment>
        ))}
        {state.stage === "delivered" && (
          <>
            <SequentialChatBubbles
              items={[
                {
                  speaker: "yacy",
                  kind: "DELIVERED_WITH_SWAP",
                  context: {},
                  fallback: "Delivered — including your approved Meadow swap.",
                  time: "18:40",
                },
                {
                  speaker: "yacy",
                  kind: "DELIVERED_FEEDBACK_PROMPT",
                  context: {},
                  fallback: DELIVERED_FEEDBACK_PROMPT_FALLBACK,
                  time: "18:40",
                },
              ]}
            />
            {feedbackExchanges.map((exchange, index) => (
              <Fragment key={`feedback-${index}-${exchange.time}`}>
                <ChatBubble speaker="maya" text={exchange.text} time={exchange.time} />
                {exchange.reply ? (
                  <ChatBubble speaker="yacy" text={exchange.reply} time={exchange.time} />
                ) : !exchange.replyKind && !exchange.useAssistant ? (
                  // Classifying what this reply is actually about before we even
                  // know which kind to generate wording for - see classifyFeedback.
                  <TypingBubble speaker="yacy" />
                ) : exchange.useAssistant ? (
                  <AssistantChatBubble
                    customerId={CUSTOMER_ID}
                    orderId={state.order?.order_id}
                    message={exchange.text}
                    fallback={exchange.replyFallback ?? "Thanks for your message! Let us know if there's anything else we can help with."}
                    time={exchange.time}
                    narrativeStage="delivered"
                    onResolved={(text) => recordExchangeReply(setFeedbackExchanges, index, text)}
                  />
                ) : (
                  <GeneratedChatBubble
                    speaker="yacy"
                    kind={exchange.replyKind ?? "FEEDBACK_THANKS"}
                    context={{
                      ...exchange.replyContext,
                      conversation_history: exchange.history,
                    }}
                    fallback={exchange.replyFallback ?? "Thanks for your feedback — we’ll remember that for next time!"}
                    time={exchange.time}
                    onResolved={(text) => recordExchangeReply(setFeedbackExchanges, index, text)}
                  />
                )}
              </Fragment>
            ))}
          </>
        )}
      </div>
      <div className="chat-bottom">
        <PhoneComposer
          onSettings={() => onNavigate("preferences")}
          onAssistant={() => onNavigate("assistant")}
          onSend={
            state.stage === "order-confirmation"
              ? (text) => setOrderExchanges((prev) => [...prev, { text, time: currentTime(), history: [] }])
              : showConfirmation
                ? (text) => setChatExchanges((prev) => [...prev, { text, time: currentTime(), history: [] }])
                : state.stage === "delivered"
                  ? (text) => {
                      // Appended immediately (no replyKind yet) so the customer's own
                      // message shows right away; it renders as a typing indicator
                      // until classification + planning below fill in the reply.
                      const index = feedbackExchanges.length;
                      const historySnapshot = buildConversationHistory(orderExchanges, chatExchanges, feedbackExchanges);
                      setFeedbackExchanges((prev) => [...prev, { text, time: currentTime(), history: historySnapshot }]);

                      // The scripted "Was today's swap right for you?" prompt isn't part of
                      // historySnapshot (it's rendered separately, not a tracked exchange),
                      // so the very first reply needs it added explicitly - otherwise the
                      // classifier has no idea a bare "no" is answering a satisfaction question.
                      // It must go LAST (right before the customer's reply), not first -
                      // historySnapshot can already contain earlier, unrelated chat from the
                      // order-confirmation/substitution screens, and putting the prompt before
                      // that made it look like the oldest message instead of the most recent
                      // one, so the classifier read "no" as answering the wrong thing.
                      const classificationHistory =
                        feedbackFlow.stage === "awaiting-initial"
                          ? [...historySnapshot, { speaker: "yacy" as const, text: DELIVERED_FEEDBACK_PROMPT_FALLBACK }]
                          : historySnapshot;

                      const classificationPromise: Promise<FeedbackClassification> = feedbackStageNeedsClassification(feedbackFlow.stage)
                        ? classifyFeedback(text, classificationHistory)
                        : Promise.resolve({ area: null, sentiment: "neutral" });

                      void classificationPromise.then((classification) => {
                        const plan = planFeedbackReply(text, classification, feedbackFlow, buildOrderFacts(state));
                        setFeedbackFlow(plan.nextFlow);
                        applyFeedbackPlan(setFeedbackExchanges, index, plan);
                      });
                    }
                  : undefined
          }
        />
      </div>
    </div>
  );
}

function PhoneChatHeader({
  onNavigate,
  onBack,
}: {
  onNavigate: (stage: Stage) => void;
  onBack: () => void;
}) {
  return (
    <div className="whatsapp-header">
      <button aria-label="Back" onClick={onBack}>‹</button><Logo compact />
      <button className="whatsapp-name" onClick={() => onNavigate("preferences")}><strong>Yacy <span>✓</span></strong><small>Business account · WhatsApp</small></button>
      <button className="header-menu" aria-label="Preferences" onClick={() => onNavigate("preferences")}>⋮</button>
    </div>
  );
}

function PhoneComposer({
  onSettings,
  onAssistant,
  onSend,
  disabled,
}: {
  onSettings?: () => void;
  onAssistant?: () => void;
  onSend?: (text: string) => void;
  disabled?: boolean;
}) {
  const [value, setValue] = useState("");

  const handleSend = () => {
    if (!onSend || disabled || !value.trim()) return;
    onSend(value.trim());
    setValue("");
  };

  return (
    <div className="composer">
      {onAssistant && <button aria-label="Open Yacy Assistant" onClick={onAssistant}>✦</button>}
      <input
        aria-label="Message"
        placeholder="Message"
        value={value}
        disabled={disabled}
        onChange={(event) => setValue(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter") {
            event.preventDefault();
            handleSend();
          }
        }}
      />
      {onSettings && <button aria-label="Open preferences" onClick={onSettings}>＋</button>}
      {onSend && (
        <button aria-label="Send message" className="composer-send" disabled={disabled || !value.trim()} onClick={handleSend}>
          ➤
        </button>
      )}
    </div>
  );
}

function ChatBubble({ speaker, text, time }: { speaker: Speaker; text: string; time: string }) {
  const outgoing = speaker === "maya";
  return (
    <div className={`chat-bubble ${outgoing ? "chat-bubble--outgoing" : ""} ${speaker === "marcus" ? "chat-bubble--marcus" : ""}`}>
      {speaker === "marcus" && <strong className="bubble-author">MARCUS</strong>}
      <p>{text}</p><span className="message-time">{time}{outgoing ? " ✓✓" : ""}</span>
    </div>
  );
}

function TypingBubble({ speaker }: { speaker: Speaker }) {
  const outgoing = speaker === "maya";
  return (
    <div
      className={`chat-bubble chat-bubble--typing ${outgoing ? "chat-bubble--outgoing" : ""} ${speaker === "marcus" ? "chat-bubble--marcus" : ""}`}
      aria-label="Typing…"
      role="status"
    >
      <span />
      <span />
      <span />
    </div>
  );
}

/**
 * A ChatBubble whose wording is generated on the fly by the backend's LLM
 * endpoint, given a message `kind` and structured `context` facts. Shows a
 * WhatsApp-style typing indicator while the request is in flight, then
 * fades in the generated text (or `fallback`, if the LLM call failed) -
 * never a visible text swap, and never stuck waiting forever.
 *
 * `onResolved` fires once, the moment this bubble's text is ready - used by
 * `SequentialChatBubbles` to only reveal (and start generating) the next
 * message once this one has actually finished, instead of every bubble in a
 * group showing "typing…" at the same time.
 */
function GeneratedChatBubble({
  speaker,
  kind,
  context,
  fallback,
  time,
  onResolved,
}: {
  speaker: Speaker;
  kind: string;
  context: Record<string, unknown>;
  fallback: string;
  time: string;
  onResolved?: (text: string) => void;
}) {
  const { text, isPending } = useGeneratedMessage(kind, context, fallback);

  useEffect(() => {
    if (!isPending) onResolved?.(text);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isPending]);

  if (isPending) return <TypingBubble speaker={speaker} />;
  return <ChatBubble speaker={speaker} text={text} time={time} />;
}

function AssistantChatBubble({
  customerId,
  orderId,
  message,
  fallback,
  time,
  narrativeStage = "current",
  onResolved,
}: {
  customerId: string;
  orderId: string | null | undefined;
  message: string;
  fallback: string;
  time: string;
  narrativeStage?: "order_placed" | "current" | "delivered";
  onResolved?: (text: string) => void;
}) {
  const { text, isPending } = useAssistantReply(customerId, orderId, message, fallback, narrativeStage);

  useEffect(() => {
    if (!isPending) onResolved?.(text);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isPending]);

  if (isPending) return <TypingBubble speaker="yacy" />;
  return <ChatBubble speaker="yacy" text={text} time={time} />;
}

interface SequencedBubbleSpec {
  speaker: Speaker;
  kind: string;
  context: Record<string, unknown>;
  fallback: string;
  time: string;
}

/**
 * Renders a group of GeneratedChatBubbles one at a time: the next bubble
 * isn't even mounted (so its LLM call hasn't started) until the previous one
 * has resolved, matching how a real person sends one WhatsApp message, waits,
 * then sends the next - rather than every reply "typing" simultaneously.
 */
function SequentialChatBubbles({ items }: { items: SequencedBubbleSpec[] }) {
  const sequenceKey = items.map((item) => `${item.kind}::${JSON.stringify(item.context)}`).join("|");
  const [revealedCount, setRevealedCount] = useState(1);

  useEffect(() => {
    setRevealedCount(1);
  }, [sequenceKey]);

  return (
    <>
      {items.slice(0, revealedCount).map((item, index) => (
        <GeneratedChatBubble
          key={`${item.kind}-${index}`}
          speaker={item.speaker}
          kind={item.kind}
          context={item.context}
          fallback={item.fallback}
          time={item.time}
          onResolved={() => setRevealedCount((count) => Math.max(count, index + 2))}
        />
      ))}
    </>
  );
}

function ServiceConsole({
  state,
  onChooseResolution,
  reply,
  setReply,
  onSendReply,
  orderExchanges,
  chatExchanges,
  feedbackExchanges,
}: {
  state: JourneyState;
  onChooseResolution: (choice: string) => void;
  reply: string;
  setReply: (value: string) => void;
  onSendReply: () => void;
  orderExchanges: ComposerExchange[];
  chatExchanges: ComposerExchange[];
  feedbackExchanges: ComposerExchange[];
}) {
  const inHumanAssistance = state.caseDetails?.outcome === "HUMAN_ASSISTANCE_REQUIRED";
  const preference = state.preferences?.product_preferences[0];
  const yacyAlternative = state.inventory?.products.find((product) => product.sku_id === "SKU-YACY-LF-1L");
  const originalProduct = state.inventory?.products.find((product) => product.sku_id === ORIGINAL_SKU);
  const originalPrice = originalProduct?.unit_price ?? 3.49;
  const meadowStock = state.caseDetails
    ? state.caseDetails.valid_alternatives.reduce((sum, product) => sum + product.available_quantity, 0)
    : state.inventory?.products
        .filter((product) => product.brand === "Meadow" && product.category === "Milk" && product.sku_id !== ORIGINAL_SKU)
        .reduce((sum, product) => sum + (product.stock_by_store["Leeds Central"] ?? 0), 0);

  return (
    <div className="service-window">
      <div className="console-topbar">
        <span className="app-grid">⠿</span><Logo compact /><strong className="console-brand">Yacy Care</strong>
        <span className="console-tab">Cases</span><span>Customers</span><span>Orders</span><span>Knowledge</span>
        <div className="console-user">M</div>
      </div>
      <div className="console-subbar"><span>Home</span><strong>Exceptions inbox</strong>{inHumanAssistance && <span>Maya · Case</span>}</div>
      <div className="console-workspace">
        <aside className="case-inbox">
          <div className="inbox-title"><strong>Omnichannel inbox · exceptions</strong><small>Yacy Care</small></div>
          {inHumanAssistance ? (
            <div className="case-row case-row--active"><span className="case-avatar">M</span><div><strong>{state.customer?.customer_name} · {state.customer?.loyalty_tier}</strong><small>No Meadow in stock · preference</small><em>Preference　{preference?.preferred_brand}　{state.customer?.contact_details.preferred_contact_channel}</em></div></div>
          ) : (
            <div className="empty-inbox"><span>✓</span><strong>No assistance required</strong></div>
          )}
        </aside>

        <section className="console-conversation">
          {inHumanAssistance ? (
            <>
              <div className="conversation-heading"><strong>Maya</strong><span>· WhatsApp · {state.caseDetails?.resolved ? "Case resolved" : "Case in progress"}</span></div>
              <div className="console-messages" aria-live="polite">
                {state.timeline.filter((entry) => entry.kind !== "AUDIT").map((entry, index) => (
                  <span className="event-chip" key={`${entry.kind}-${entry.event_id ?? entry.notification_id ?? "timeline"}-${index}`}>
                    {formatTimelineTime(entry.timestamp)} · {entry.message ?? entry.event_type ?? entry.status ?? "Order update"}
                  </span>
                ))}
                {/* Chronological: the order-confirmation message and its chat
                    happen before the out-of-stock case even exists - also
                    scripted-only in the phone view, so mirrored the same way. */}
                {state.order && (
                  <GeneratedChatBubble
                    speaker="yacy"
                    kind="ORDER_CONFIRMATION"
                    context={{
                      customer_name: state.customer?.customer_name,
                      order_id: state.order.order_id,
                      delivery_date: formatDeliveryDate(state.order.delivery_date),
                      delivery_slot: formatSlot(state.order.delivery_slot),
                    }}
                    fallback={`Hi ${state.customer?.customer_name} 👋 Your order ${state.order.order_id} is confirmed for ${formatDeliveryDate(state.order.delivery_date)}, ${formatSlot(state.order.delivery_slot)}. We’ll message you here if anything changes.`}
                    time="19:31"
                  />
                )}
                {orderExchanges.map((exchange, index) => (
                  <Fragment key={`console-order-${index}-${exchange.time}`}>
                    <ChatBubble speaker="maya" text={exchange.text} time={exchange.time} />
                    {exchange.reply && <ChatBubble speaker="yacy" text={exchange.reply} time={exchange.time} />}
                  </Fragment>
                ))}
                {state.messages.map((message) => message.speaker === "system"
                  ? <span className="event-chip" key={message.id}>{message.text}</span>
                  : <ChatBubble key={message.id} speaker={message.speaker} text={message.text} time={message.time} />)}
                {/* The phone view renders this same resolution-choice confirmation
                    inline (not via state.messages), so it's mirrored here too with
                    the same kind/context - the backend's response cache keeps the
                    wording identical between the two panels. */}
                {state.caseDetails?.resolved && state.notification?.responded_resolution_choice && (
                  <>
                    <ChatBubble
                      speaker="maya"
                      text={state.notification.responded_resolution_choice === "WALLET_CREDIT" ? "Instant Yacy wallet credit" : "Refund to card"}
                      time={currentTime()}
                    />
                    <SequentialChatBubbles
                      items={[
                        {
                          speaker: "marcus",
                          kind: "HUMAN_ASSISTANCE_CASE_UPDATE",
                          context: {},
                          fallback: "Your choice is recorded. I’ve updated the case.",
                          time: currentTime(),
                        },
                        {
                          speaker: "yacy",
                          kind: "RESOLUTION_CONFIRMATION",
                          context: {
                            resolution_choice: state.notification.responded_resolution_choice,
                            amount: originalPrice,
                            currency: "GBP",
                          },
                          fallback:
                            state.notification.responded_resolution_choice === "WALLET_CREDIT"
                              ? `${formatPrice(originalPrice)} added to your Yacy wallet.`
                              : `Your ${formatPrice(originalPrice)} card refund is being processed.`,
                          time: currentTime(),
                        },
                      ]}
                    />
                  </>
                )}
                {/* Mirrors the free-text WhatsApp chat Maya has with the assistant
                    after the case is handed off - without this, Marcus could only
                    ever see the scripted case messages, never what Maya actually typed. */}
                {chatExchanges.map((exchange, index) => (
                  <Fragment key={`console-chat-${index}-${exchange.time}`}>
                    <ChatBubble speaker="maya" text={exchange.text} time={exchange.time} />
                    {exchange.reply && <ChatBubble speaker="yacy" text={exchange.reply} time={exchange.time} />}
                  </Fragment>
                ))}
                {/* Also scripted-only in the phone view, like the resolution block above. */}
                {state.stage === "delivered" && (
                  <>
                    <GeneratedChatBubble
                      speaker="yacy"
                      kind="DELIVERED_WITH_SWAP"
                      context={{}}
                      fallback="Delivered — including your approved Meadow swap."
                      time={currentTime()}
                    />
                    <GeneratedChatBubble
                      speaker="yacy"
                      kind="DELIVERED_FEEDBACK_PROMPT"
                      context={{}}
                      fallback={DELIVERED_FEEDBACK_PROMPT_FALLBACK}
                      time={currentTime()}
                    />
                  </>
                )}
                {feedbackExchanges.map((exchange, index) => (
                  <Fragment key={`console-feedback-${index}-${exchange.time}`}>
                    <ChatBubble speaker="maya" text={exchange.text} time={exchange.time} />
                    {exchange.reply && <ChatBubble speaker="yacy" text={exchange.reply} time={exchange.time} />}
                  </Fragment>
                ))}
              </div>
              <div className="console-compose">
                <span>Reply</span><span>Note</span><span>Disposition</span>
                <div><input value={reply} onChange={(event) => setReply(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") onSendReply(); }} placeholder="Type a reply…" disabled={state.caseDetails?.resolved} /><button onClick={onSendReply} aria-label="Send reply" disabled={state.caseDetails?.resolved}>➤</button></div>
              </div>
            </>
          ) : (
            <div className="neutral-console"><span className="neutral-check">✓</span><strong>No assistance required</strong></div>
          )}
        </section>

        <aside className="console-context">
          {inHumanAssistance ? (
            <>
              <section className="console-card">
                <span className="console-label">CUSTOMER 360</span>
                <div className="customer-title"><span className="case-avatar">M</span><div><strong>Maya</strong><small>Gold · WhatsApp</small></div></div>
                <dl>
                  <div><dt>Preference</dt><dd>{preference?.preferred_brand} only ✓</dd></div>
                  <div><dt>Delivery</dt><dd>{formatSlot(state.order?.delivery_slot ?? "")}</dd></div>
                  <div><dt>Meadow stock</dt><dd>{meadowStock} available</dd></div>
                  <div><dt>Original item</dt><dd>{originalProduct?.product_name ?? "Out of stock"}</dd></div>
                </dl>
              </section>
              <section className="console-card blocked-card">
                <span className="console-label">BLOCKED SUBSTITUTION</span>
                <strong>{yacyAlternative?.product_name}</strong>
                <p>{state.caseDetails?.rejected_alternatives.find((item) => item.sku_id === yacyAlternative?.sku_id)?.reason ?? `${yacyAlternative?.brand} is outside Maya’s saved Meadow preference.`}</p>
              </section>
              <section className="console-card assist-card">
                <span className="console-label">AGENT ASSIST · NEXT BEST ACTION</span>
                <strong>Suggested for Maya</strong>
                {state.caseDetails?.human_assistance?.resolution_options.map((option) => (
                  <button className="assist-action" key={option.type} onClick={() => onChooseResolution(option.type)} disabled={Boolean(state.mutation) || state.caseDetails?.resolved}>
                    <span><strong>{option.type === "WALLET_CREDIT" ? "Offer instant wallet credit" : "Offer refund to card"}</strong><small>{option.description}</small></span>
                    <b>{formatPrice(option.amount, option.currency)}</b>
                  </button>
                ))}
                {state.notification?.responded_resolution_choice && <p className="assist-selected">Maya chose {state.notification.responded_resolution_choice.replace("_", " ").toLowerCase()} ✓</p>}
                <div className="assist-note"><strong>Note: rule already on profile</strong><small>No need to ask about her preferences.</small></div>
              </section>
            </>
          ) : (
            <section className="console-card neutral-context">
              <span className="console-label">LIVE CASES</span>
              <strong>No assistance required</strong>
            </section>
          )}
        </aside>
      </div>
    </div>
  );
}
