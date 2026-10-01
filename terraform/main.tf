terraform {
  required_version = ">= 1.5.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.40"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

locals {
  required_apis = [
    "aiplatform.googleapis.com",
    "cloudtrace.googleapis.com",
    "logging.googleapis.com",
    "pubsub.googleapis.com",
    "run.googleapis.com",
    "secretmanager.googleapis.com",
    "storage.googleapis.com",
  ]
}

resource "google_project_service" "apis" {
  for_each           = toset(local.required_apis)
  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

# --- Least-privilege runtime service account ----------------------------------

resource "google_service_account" "agent_runtime" {
  account_id   = "${var.name_prefix}-runtime"
  display_name = "The Good Neighbour — Agent Runtime SA"
}

resource "google_project_iam_member" "runtime_roles" {
  for_each = toset([
    "roles/aiplatform.user",
    "roles/cloudtrace.agent",
    "roles/logging.logWriter",
    "roles/pubsub.subscriber",
  ])
  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.agent_runtime.email}"
}

# --- Secret Manager (for optional AI Studio key or webhook secrets) -----------

resource "google_secret_manager_secret" "api_key" {
  secret_id = "GOOGLE_API_KEY"
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_iam_member" "runtime_secret_access" {
  secret_id = google_secret_manager_secret.api_key.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.agent_runtime.email}"
}

# --- Staging bucket for Vertex AI Agent Engine deployments -------------------

resource "google_storage_bucket" "staging" {
  name                        = "${var.project_id}-${var.name_prefix}-staging"
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = false
  depends_on                  = [google_project_service.apis]
}

resource "google_storage_bucket_iam_member" "runtime_staging_reader" {
  bucket = google_storage_bucket.staging.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.agent_runtime.email}"
}

# --- Ambient Pub/Sub intake topic ---------------------------------------------

resource "google_pubsub_topic" "intake" {
  name       = "${var.name_prefix}-intake"
  depends_on = [google_project_service.apis]
}

# --- Optional Cloud Run push endpoint (off by default) ------------------------

resource "google_cloud_run_v2_service" "push_endpoint" {
  count    = var.enable_push_endpoint ? 1 : 0
  name     = "${var.name_prefix}-push"
  location = var.region
  ingress  = "INGRESS_TRAFFIC_INTERNAL_ONLY"

  template {
    service_account = google_service_account.agent_runtime.email
    containers {
      image = var.push_endpoint_image
      env {
        name  = "GOOGLE_GENAI_USE_ENTERPRISE"
        value = "TRUE"
      }
      env {
        name  = "GOOGLE_CLOUD_PROJECT"
        value = var.project_id
      }
      env {
        name  = "GOOGLE_CLOUD_LOCATION"
        value = var.region
      }
      env {
        name  = "AGENT_ENGINE_ID"
        value = var.agent_engine_id
      }
      env {
        name  = "GN_TRACE_EXPORTER"
        value = "gcp"
      }
    }
  }
  depends_on = [google_project_service.apis]
}

resource "google_cloud_run_v2_service_iam_member" "pubsub_invoker" {
  count    = var.enable_push_endpoint ? 1 : 0
  name     = google_cloud_run_v2_service.push_endpoint[0].name
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.agent_runtime.email}"
}

resource "google_pubsub_subscription" "intake_push" {
  count = var.enable_push_endpoint ? 1 : 0
  name  = "${var.name_prefix}-intake-push"
  topic = google_pubsub_topic.intake.id

  ack_deadline_seconds = 60
  push_config {
    push_endpoint = "${google_cloud_run_v2_service.push_endpoint[0].uri}/pubsub/push"
    oidc_token {
      service_account_email = google_service_account.agent_runtime.email
    }
  }
}
