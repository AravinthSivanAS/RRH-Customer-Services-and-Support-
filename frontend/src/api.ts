export interface CustomerProfile {
  customer_id: string;
  customer_name: string;
  customer_since: string;
  loyalty_tier: string;
  preferred_store_id: string;
  contact_details: {
    preferred_contact_channel: string;
    whatsapp_opt_in: boolean;
  };
  preferences: CustomerPreferences;
  active_order: OrderSnapshot | null;
}

export interface ProductPreference {
  product_category: string;
  preferred_product_name: string;
  preferred_brand: string;
  preferred_sku_id: string;
  substitution_rule: string;
  price_increase_limit_percentage: number;
  notes: string | null;
}

export interface ProductPreferenceUpdate {
  product_category: string;
  preferred_brand: string;
  preferred_sku_id: string;
  substitution_rule: string;
  price_increase_limit_percentage?: number;
  preferred_product_name?: string | null;
  notes?: string | null;
}

export interface CustomerPreferences {
  allow_preference_based_recommendations: boolean;
  allow_substitution_contact: boolean;
  dietary_requirements: string[];
  product_preferences: ProductPreference[];
}

export interface InventoryProduct {
  sku_id: string;
  product_name: string;
  brand: string;
  category: string;
  unit_price: number;
  current_stock_status: string;
  stock_by_store: Record<string, number>;
}

export interface StoreInfo {
  store_id: string;
  store_name: string;
}

export interface InventoryResponse {
  inventory_last_updated: string;
  stores: StoreInfo[];
  products: InventoryProduct[];
}

export interface StoreStock {
  sku_id: string;
  store_id: string;
  store_name: string;
  available_quantity: number;
}

export interface OrderItem {
  sku_id: string;
  product_name: string;
  quantity: number;
  unit_price: number;
  item_status: string;
  substitution_reason?: string | null;
  decision_source?: string | null;
  original_sku_id?: string | null;
}

export interface OrderSnapshot {
  order_id: string;
  customer_id: string;
  order_date: string;
  delivery_date: string;
  delivery_slot: string;
  fulfilment_store_id: string;
  status: string;
  payment_status: string;
  currency: string;
  items: OrderItem[];
  order_total?: number;
}

export interface AlternativeOption {
  sku_id: string;
  product_name: string;
  brand: string;
  unit_price: number;
  available_quantity: number;
  score: number;
  reasons: string[];
}

export interface RejectedAlternative {
  sku_id: string;
  product_name: string;
  brand: string;
  reason: string;
}

export interface ResolutionOption {
  type: string;
  amount: number;
  currency: string;
  description: string;
}

export interface HumanAssistance {
  routing_reason: string;
  assigned_queue: string;
  resolution_options: ResolutionOption[];
}

export interface SubstitutionCase {
  order_id: string;
  customer_id: string;
  original_sku_id: string;
  store_id: string;
  requested_quantity: number;
  outcome: string;
  rule_applied: string;
  valid_alternatives: AlternativeOption[];
  rejected_alternatives: RejectedAlternative[];
  notification_id: string;
  human_assistance: HumanAssistance | null;
  resolved: boolean;
  created_at: string;
}

export interface ShortPickResponse {
  event_id: string;
  order_id: string;
  customer_id: string;
  original_product: {
    sku_id: string;
    product_name: string;
    status: string;
  };
  customer_rule: {
    rule: string;
    preferred_brand: string | null;
  };
  decision: {
    outcome: string;
    valid_alternatives: AlternativeOption[];
    rejected_alternatives: RejectedAlternative[];
  };
  notification: {
    notification_id: string;
    channel: string;
    status: string;
  };
  human_assistance: HumanAssistance | null;
  next_action: string;
}

export interface NotificationResponse {
  notification_id: string;
  notification_type: "SUBSTITUTION_OFFER" | "HUMAN_ASSISTANCE";
  channel: string;
  customer_id: string;
  order_id: string;
  status: string;
  message: string;
  alternatives: AlternativeOption[];
  resolution_options: ResolutionOption[];
  responded_sku_id: string | null;
  responded_resolution_choice: string | null;
  created_at: string;
  updated_at: string;
}

export interface TimelineEntry {
  kind: "EVENT" | "AUDIT" | "NOTIFICATION";
  timestamp: string;
  event_id?: string;
  event_type?: string;
  notification_id?: string;
  status?: string;
  message?: string;
  sku_id?: string | null;
  store_id?: string | null;
  payload?: Record<string, unknown>;
}

export interface DemoResetResponse {
  status: string;
  message: string;
}

export interface NoMeadowSetupResponse {
  status: string;
  scenario: string;
  store_id: string;
  unavailable_skus: string[];
}

export interface GenerateMessageRequest {
  kind: string;
  context?: Record<string, unknown>;
  fallback: string;
}

export interface GenerateMessageResponse {
  message: string;
  source: "llm" | "fallback";
}

export interface SubstitutionSelectionRequest {
  customer_id: string;
  original_sku_id: string;
  selected_sku_id: string;
  notification_id: string;
}

export interface NotificationActionRequest {
  selected_sku_id?: string;
  resolution_choice?: string;
}

export interface ShortPickRequest {
  event_type: "SHORT_PICK_RECORDED";
  event_time: string;
  order_id: string;
  customer_id: string;
  store_id: string;
  sku_id: string;
  requested_quantity: number;
  available_quantity: number;
  picker_id: string;
}

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      ...init,
      headers: {
        ...(init?.body ? { "Content-Type": "application/json" } : {}),
        ...init?.headers,
      },
    });
  } catch {
    throw new ApiError("The backend is unavailable. Check that FastAPI is running.", 0);
  }

  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try {
      const body: unknown = await response.json();
      if (
        typeof body === "object" &&
        body !== null &&
        "detail" in body &&
        typeof body.detail === "string"
      ) {
        message = body.detail;
      }
    } catch {
      // Keep the HTTP status message when the server did not return JSON.
    }
    throw new ApiError(message, response.status);
  }

  return (await response.json()) as T;
}

export const api = {
  health: () => request<{ status: string; service: string }>("/health"),
  customer: (customerId: string) =>
    request<CustomerProfile>(`/api/customers/${encodeURIComponent(customerId)}`),
  preferences: (customerId: string) =>
    request<CustomerPreferences>(
      `/api/customers/${encodeURIComponent(customerId)}/preferences`,
    ),
  savePreference: (customerId: string, payload: ProductPreferenceUpdate) =>
    request<CustomerPreferences>(
      `/api/customers/${encodeURIComponent(customerId)}/preferences`,
      { method: "POST", body: JSON.stringify(payload) },
    ),
  inventory: () => request<InventoryResponse>("/api/inventory"),
  productStock: (skuId: string, storeId: string) =>
    request<StoreStock>(
      `/api/inventory/${encodeURIComponent(skuId)}/stores/${encodeURIComponent(storeId)}`,
    ),
  order: (orderId: string) =>
    request<OrderSnapshot>(`/api/orders/${encodeURIComponent(orderId)}`),
  substitutionOptions: (orderId: string) =>
    request<SubstitutionCase>(
      `/api/orders/${encodeURIComponent(orderId)}/substitution-options`,
    ),
  shortPick: (payload: ShortPickRequest) =>
    request<ShortPickResponse>("/api/events/short-pick", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  selectSubstitution: (orderId: string, payload: SubstitutionSelectionRequest) =>
    request<OrderSnapshot>(
      `/api/orders/${encodeURIComponent(orderId)}/substitution-selection`,
      { method: "POST", body: JSON.stringify(payload) },
    ),
  respondToNotification: (notificationId: string, payload: NotificationActionRequest) =>
    request<NotificationResponse>(
      `/api/notifications/${encodeURIComponent(notificationId)}/simulate-response`,
      { method: "POST", body: JSON.stringify(payload) },
    ),
  notification: (notificationId: string) =>
    request<NotificationResponse>(
      `/api/notifications/${encodeURIComponent(notificationId)}`,
    ),
  timeline: (orderId: string) =>
    request<TimelineEntry[]>(`/api/orders/${encodeURIComponent(orderId)}/timeline`),
  resetDemo: () =>
    request<DemoResetResponse>("/api/demo/reset", { method: "POST" }),
  prepareNoMeadowScenario: () =>
    request<NoMeadowSetupResponse>("/api/demo/scenarios/no-meadow", {
      method: "POST",
    }),
  generateMessage: (payload: GenerateMessageRequest) =>
    request<GenerateMessageResponse>("/api/messages/generate", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
};
