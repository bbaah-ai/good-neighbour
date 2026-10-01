variable "project_id" {
  description = "Google Cloud project ID."
  type        = string
}

variable "region" {
  description = "Default Google Cloud region for Vertex AI, Cloud Run, and Storage."
  type        = string
  default     = "us-central1"
}

variable "name_prefix" {
  description = "Resource name prefix."
  type        = string
  default     = "good-neighbour"
}

variable "agent_engine_id" {
  description = "Vertex AI Agent Engine resource ID (populated after `adk deploy agent_engine`)."
  type        = string
  default     = ""
}

variable "enable_push_endpoint" {
  description = "Deploy the optional Cloud Run Pub/Sub push receiver and OIDC push subscription."
  type        = bool
  default     = false
}

variable "push_endpoint_image" {
  description = "Container image URI for the optional Cloud Run push endpoint."
  type        = string
  default     = "us-docker.pkg.dev/cloudrun/container/hello"
}
