output "runtime_service_account_email" {
  description = "Least-privilege service account for the agent runtime."
  value       = google_service_account.agent_runtime.email
}

output "staging_bucket" {
  description = "Cloud Storage staging bucket for `adk deploy agent_engine`."
  value       = "gs://${google_storage_bucket.staging.name}"
}

output "pubsub_intake_topic" {
  description = "Full resource name of the Pub/Sub intake topic."
  value       = google_pubsub_topic.intake.id
}

output "push_endpoint_uri" {
  description = "Cloud Run push endpoint URI when enable_push_endpoint = true."
  value       = var.enable_push_endpoint ? google_cloud_run_v2_service.push_endpoint[0].uri : null
}
