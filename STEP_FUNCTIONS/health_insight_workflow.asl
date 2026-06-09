{
    "Comment": "HealthInsight AI Platform — Healthcare Data Extraction and Claims Processing Workflow v1.0. Full pipeline: multi-source extraction → PHI classification → quality gate → FHIR normalization → parallel Strands AI agents → mandatory human review → action.",
    "StartAt": "ValidateAndAuthenticateTenant",
    "States": {
        "ValidateAndAuthenticateTenant": {
            "Type": "Task",
            "Resource": "arn:aws:lambda:us-east-2:596272105033:function:hi-validate-tenant",
            "Comment": "Validates tenant JWT, resolves tenant config, verifies BAA is active before any PHI is accessed",
            "Parameters": {
                "tenant_id.$": "$.tenant_id",
                "batch_id.$": "$.batch_id",
                "request_context.$": "$.request_context"
            },
            "ResultPath": "$.tenant_config",
            "Retry": [
                {
                    "ErrorEquals": [
                        "Lambda.ServiceException",
                        "Lambda.AWSLambdaException"
                    ],
                    "IntervalSeconds": 2,
                    "MaxAttempts": 3,
                    "BackoffRate": 2
                }
            ],
            "Catch": [
                {
                    "ErrorEquals": [
                        "TenantAuthError",
                        "BAANotFoundError",
                        "BAAExpiredError"
                    ],
                    "Next": "WorkflowFailed",
                    "ResultPath": "$.error"
                }
            ],
            "Next": "DetermineExtractionStrategy"
        },
        "DetermineExtractionStrategy": {
            "Type": "Choice",
            "Comment": "Route to the appropriate extraction method based on the data source type configured for this tenant",
            "Choices": [
                {
                    "Variable": "$.extraction_config.source_type",
                    "StringEquals": "FHIR_R4_API",
                    "Next": "ExtractFromFHIRAPI"
                },
                {
                    "Variable": "$.extraction_config.source_type",
                    "StringEquals": "HL7_V2",
                    "Next": "ExtractFromHL7Feed"
                },
                {
                    "Variable": "$.extraction_config.source_type",
                    "StringEquals": "BULK_EXPORT",
                    "Next": "TriggerBulkExport"
                },
                {
                    "Variable": "$.extraction_config.source_type",
                    "StringEquals": "FLAT_FILE",
                    "Next": "ExtractFromFlatFile"
                },
                {
                    "Variable": "$.extraction_config.source_type",
                    "StringEquals": "MULTI_SOURCE",
                    "Next": "ExtractFromMultipleSources"
                }
            ],
            "Default": "ExtractFromFHIRAPI"
        },
        "ExtractFromFHIRAPI": {
            "Type": "Task",
            "Resource": "arn:aws:lambda:us-east-2:596272105033:function:hi-fhir-extractor",
            "Comment": "Connects to EHR FHIR R4 API, paginates through resource bundles (Patient, Encounter, Claim, Condition, Procedure, Coverage), stores raw NDJSON to S3 raw zone with tenant isolation",
            "Parameters": {
                "tenant_id.$": "$.tenant_id",
                "fhir_endpoint.$": "$.extraction_config.fhir_endpoint",
                "resource_types": [
                    "Patient",
                    "Encounter",
                    "Claim",
                    "Condition",
                    "Procedure",
                    "Coverage",
                    "Organization",
                    "Practitioner"
                ],
                "since_date.$": "$.extraction_config.since_date",
                "page_size": 100,
                "s3_raw_prefix.$": "States.Format('raw/{}/{}/fhir/', $.tenant_id, $.batch_id)"
            },
            "ResultPath": "$.extraction_result",
            "TimeoutSeconds": 300,
            "Retry": [
                {
                    "ErrorEquals": [
                        "FHIRTimeoutError",
                        "Lambda.ServiceException"
                    ],
                    "IntervalSeconds": 5,
                    "MaxAttempts": 3,
                    "BackoffRate": 2
                },
                {
                    "ErrorEquals": [
                        "FHIRRateLimitError"
                    ],
                    "IntervalSeconds": 30,
                    "MaxAttempts": 5,
                    "BackoffRate": 1.5
                }
            ],
            "Catch": [
                {
                    "ErrorEquals": [
                        "FHIRAPIUnavailableError",
                        "FHIRAuthError"
                    ],
                    "Next": "FallbackToHL7Extraction",
                    "ResultPath": "$.fhir_error"
                },
                {
                    "ErrorEquals": [
                        "States.ALL"
                    ],
                    "Next": "WorkflowFailed",
                    "ResultPath": "$.error"
                }
            ],
            "Next": "ClassifyPHIFields"
        },
        "FallbackToHL7Extraction": {
            "Type": "Pass",
            "Comment": "Log FHIR failure reason and reroute to HL7 v2 extraction fallback",
            "Parameters": {
                "extraction_config.$": "$.extraction_config",
                "fallback_reason.$": "$.fhir_error",
                "extraction_method": "HL7_V2_FALLBACK"
            },
            "ResultPath": "$.fallback_context",
            "Next": "ExtractFromHL7Feed"
        },
        "ExtractFromHL7Feed": {
            "Type": "Task",
            "Resource": "arn:aws:lambda:us-east-2:596272105033:function:hi-hl7-extractor",
            "Comment": "Processes HL7 v2 messages from SQS queue or SFTP drop. Parses ADT (admissions/discharges), ORM (orders), ORU (results), DFT (billing) message types and maps to FHIR-compatible structures",
            "Parameters": {
                "tenant_id.$": "$.tenant_id",
                "hl7_source.$": "$.extraction_config.hl7_source",
                "message_types": [
                    "ADT^A01",
                    "ADT^A08",
                    "ADT^A11",
                    "ORM^O01",
                    "ORU^R01",
                    "DFT^P03",
                    "BAR^P01"
                ],
                "since_date.$": "$.extraction_config.since_date",
                "s3_raw_prefix.$": "States.Format('raw/{}/{}/hl7/', $.tenant_id, $.batch_id)"
            },
            "ResultPath": "$.extraction_result",
            "TimeoutSeconds": 300,
            "Retry": [
                {
                    "ErrorEquals": [
                        "Lambda.ServiceException"
                    ],
                    "IntervalSeconds": 5,
                    "MaxAttempts": 3,
                    "BackoffRate": 2
                }
            ],
            "Catch": [
                {
                    "ErrorEquals": [
                        "HL7ParseError",
                        "HL7ConnectionError",
                        "HL7SchemaError"
                    ],
                    "Next": "FallbackToFlatFile",
                    "ResultPath": "$.hl7_error"
                },
                {
                    "ErrorEquals": [
                        "States.ALL"
                    ],
                    "Next": "WorkflowFailed",
                    "ResultPath": "$.error"
                }
            ],
            "Next": "ClassifyPHIFields"
        },
        "FallbackToFlatFile": {
            "Type": "Pass",
            "Comment": "Log HL7 failure and reroute to flat-file extraction as final fallback",
            "Parameters": {
                "extraction_config.$": "$.extraction_config",
                "fallback_reason.$": "$.hl7_error",
                "extraction_method": "FLAT_FILE_FALLBACK"
            },
            "ResultPath": "$.fallback_context",
            "Next": "ExtractFromFlatFile"
        },
        "ExtractFromFlatFile": {
            "Type": "Task",
            "Resource": "arn:aws:lambda:us-east-2:596272105033:function:hi-flatfile-extractor",
            "Comment": "Processes CSV, pipe-delimited, or fixed-width exports uploaded to tenant S3 ingestion bucket. Validates column headers, maps to FHIR resource fields, enforces data types per column mapping config.",
            "Parameters": {
                "tenant_id.$": "$.tenant_id",
                "s3_input_bucket.$": "$.extraction_config.input_bucket",
                "s3_input_prefix.$": "$.extraction_config.input_prefix",
                "file_format.$": "$.extraction_config.file_format",
                "column_mapping.$": "$.extraction_config.column_mapping"
            },
            "ResultPath": "$.extraction_result",
            "TimeoutSeconds": 600,
            "Retry": [
                {
                    "ErrorEquals": [
                        "Lambda.ServiceException"
                    ],
                    "IntervalSeconds": 5,
                    "MaxAttempts": 2,
                    "BackoffRate": 2
                }
            ],
            "Catch": [
                {
                    "ErrorEquals": [
                        "States.ALL"
                    ],
                    "Next": "NotifyExtractionFailure",
                    "ResultPath": "$.error"
                }
            ],
            "Next": "ClassifyPHIFields"
        },
        "TriggerBulkExport": {
            "Type": "Task",
            "Resource": "arn:aws:lambda:us-east-2:596272105033:function:hi-bulk-export-trigger",
            "Comment": "Initiates FHIR Bulk Data Access ($export operation) on the EHR server. Returns polling URL for async job status.",
            "Parameters": {
                "tenant_id.$": "$.tenant_id",
                "fhir_endpoint.$": "$.extraction_config.fhir_endpoint",
                "export_types": [
                    "Patient",
                    "Encounter",
                    "Claim",
                    "Condition",
                    "Procedure"
                ],
                "since.$": "$.extraction_config.since_date",
                "output_format": "application/fhir+ndjson"
            },
            "ResultPath": "$.bulk_export_job",
            "Next": "WaitForBulkExportPolling"
        },
        "WaitForBulkExportPolling": {
            "Type": "Wait",
            "Comment": "FHIR bulk exports typically complete in 5–30 minutes. Poll every 60 seconds.",
            "Seconds": 60,
            "Next": "PollBulkExportStatus"
        },
        "PollBulkExportStatus": {
            "Type": "Task",
            "Resource": "arn:aws:lambda:us-east-2:596272105033:function:hi-bulk-export-poll",
            "Parameters": {
                "tenant_id.$": "$.tenant_id",
                "polling_url.$": "$.bulk_export_job.polling_url",
                "job_id.$": "$.bulk_export_job.job_id"
            },
            "ResultPath": "$.bulk_export_status",
            "Next": "BulkExportStatusDecision"
        },
        "BulkExportStatusDecision": {
            "Type": "Choice",
            "Choices": [
                {
                    "Variable": "$.bulk_export_status.status",
                    "StringEquals": "completed",
                    "Next": "DownloadBulkExportFiles"
                },
                {
                    "Variable": "$.bulk_export_status.status",
                    "StringEquals": "in-progress",
                    "Next": "WaitForBulkExportPolling"
                },
                {
                    "Variable": "$.bulk_export_status.status",
                    "StringEquals": "error",
                    "Next": "FallbackToHL7Extraction"
                }
            ],
            "Default": "WaitForBulkExportPolling"
        },
        "DownloadBulkExportFiles": {
            "Type": "Task",
            "Resource": "arn:aws:lambda:us-east-2:596272105033:function:hi-bulk-export-download",
            "Comment": "Downloads NDJSON bulk export files from EHR-provided S3 presigned URLs to HealthInsight tenant raw zone",
            "Parameters": {
                "tenant_id.$": "$.tenant_id",
                "export_manifest.$": "$.bulk_export_status.output_files",
                "s3_raw_prefix.$": "States.Format('raw/{}/{}/bulk/', $.tenant_id, $.batch_id)"
            },
            "ResultPath": "$.extraction_result",
            "TimeoutSeconds": 900,
            "Next": "ClassifyPHIFields"
        },
        "ExtractFromMultipleSources": {
            "Type": "Parallel",
            "Comment": "Extract from FHIR API and HL7 feed simultaneously then merge results. Used for tenants with hybrid source architectures.",
            "Branches": [
                {
                    "StartAt": "MultiBranchFHIR",
                    "States": {
                        "MultiBranchFHIR": {
                            "Type": "Task",
                            "Resource": "arn:aws:lambda:us-east-2:596272105033:function:hi-fhir-extractor",
                            "Parameters": {
                                "tenant_id.$": "$.tenant_id",
                                "source.$": "$.extraction_config.fhir_source"
                            },
                            "End": true
                        }
                    }
                },
                {
                    "StartAt": "MultiBranchHL7",
                    "States": {
                        "MultiBranchHL7": {
                            "Type": "Task",
                            "Resource": "arn:aws:lambda:us-east-2:596272105033:function:hi-hl7-extractor",
                            "Parameters": {
                                "tenant_id.$": "$.tenant_id",
                                "source.$": "$.extraction_config.hl7_source"
                            },
                            "End": true
                        }
                    }
                }
            ],
            "ResultPath": "$.multi_source_results",
            "Next": "MergeMultiSourceData"
        },
        "MergeMultiSourceData": {
            "Type": "Task",
            "Resource": "arn:aws:lambda:us-east-2:596272105033:function:hi-source-merger",
            "Comment": "Deduplicates and merges records from multiple sources using deterministic patient matching on MRN + DOB + name",
            "Parameters": {
                "tenant_id.$": "$.tenant_id",
                "source_results.$": "$.multi_source_results"
            },
            "ResultPath": "$.extraction_result",
            "Next": "ClassifyPHIFields"
        },
        "ClassifyPHIFields": {
            "Type": "Task",
            "Resource": "arn:aws:lambda:us-east-2:596272105033:function:hi-phi-classifier",
            "Comment": "Identifies and tags all 18 HIPAA Safe Harbor PHI identifiers in the extracted dataset. Logs PHI access event to CloudTrail. Enforces minimum necessary standard. No analytics proceed without completed PHI classification.",
            "Parameters": {
                "tenant_id.$": "$.tenant_id",
                "batch_id.$": "$.batch_id",
                "extracted_data_location.$": "$.extraction_result.s3_location",
                "resource_types_extracted.$": "$.extraction_result.resource_types_extracted"
            },
            "ResultPath": "$.phi_classification",
            "Retry": [
                {
                    "ErrorEquals": [
                        "Lambda.ServiceException"
                    ],
                    "IntervalSeconds": 3,
                    "MaxAttempts": 3,
                    "BackoffRate": 2
                }
            ],
            "Catch": [
                {
                    "ErrorEquals": [
                        "States.ALL"
                    ],
                    "Next": "WorkflowFailed",
                    "ResultPath": "$.error"
                }
            ],
            "Next": "AssessDataQualityPerResourceType"
        },
        "AssessDataQualityPerResourceType": {
            "Type": "Map",
            "Comment": "Run FHIR R4 validation and completeness checks in parallel for each resource type. Checks schema compliance, required field presence, value set codes, and duplicate detection.",
            "ItemsPath": "$.extraction_result.resource_types_extracted",
            "MaxConcurrency": 5,
            "Iterator": {
                "StartAt": "ValidateSingleResourceType",
                "States": {
                    "ValidateSingleResourceType": {
                        "Type": "Task",
                        "Resource": "arn:aws:lambda:us-east-2:596272105033:function:hi-data-quality-agent",
                        "Parameters": {
                            "tenant_id.$": "$.tenant_id",
                            "resource_type.$": "$$.Map.Item.Value",
                            "s3_location.$": "$.extraction_result.s3_location",
                            "validation_config": {
                                "fhir_r4_schema": true,
                                "uscdi_v3_required_fields": true,
                                "duplicate_detection": true,
                                "completeness_threshold": 0.75,
                                "value_set_validation": true
                            }
                        },
                        "Retry": [
                            {
                                "ErrorEquals": [
                                    "Lambda.ServiceException"
                                ],
                                "IntervalSeconds": 2,
                                "MaxAttempts": 3,
                                "BackoffRate": 2
                            }
                        ],
                        "End": true
                    }
                }
            },
            "ResultPath": "$.quality_reports",
            "Next": "ComputeCompositeQualityScore"
        },
        "ComputeCompositeQualityScore": {
            "Type": "Task",
            "Resource": "arn:aws:lambda:us-east-2:596272105033:function:hi-quality-aggregator",
            "Comment": "Aggregates per-resource quality scores into composite score. Weights: completeness 35%, validity 35%, consistency 20%, uniqueness 10%. Threshold 0.75 for analytics readiness.",
            "Parameters": {
                "tenant_id.$": "$.tenant_id",
                "quality_reports.$": "$.quality_reports"
            },
            "ResultPath": "$.composite_quality",
            "Next": "QualityGateDecision"
        },
        "QualityGateDecision": {
            "Type": "Choice",
            "Comment": "GATE: Analytics only proceed above 0.60. Between 0.60-0.75 proceeds with warning. Below 0.60 requires remediation before resubmission.",
            "Choices": [
                {
                    "Variable": "$.composite_quality.score",
                    "NumericGreaterThanEquals": 0.75,
                    "Next": "NormalizeFHIRResources"
                },
                {
                    "And": [
                        {
                            "Variable": "$.composite_quality.score",
                            "NumericGreaterThanEquals": 0.6
                        },
                        {
                            "Variable": "$.composite_quality.score",
                            "NumericLessThan": 0.75
                        }
                    ],
                    "Next": "SendQualityWarningAndProceed"
                }
            ],
            "Default": "NotifyQualityRemediationRequired"
        },
        "SendQualityWarningAndProceed": {
            "Type": "Task",
            "Resource": "arn:aws:lambda:us-east-2:596272105033:function:hi-quality-notifier",
            "Comment": "Quality below preferred threshold but above minimum. Proceed with warning flag active. Notify client of specific issues to address.",
            "Parameters": {
                "tenant_id.$": "$.tenant_id",
                "quality_score.$": "$.composite_quality.score",
                "issues.$": "$.composite_quality.issues",
                "notification_type": "QUALITY_WARNING",
                "proceed": true
            },
            "ResultPath": "$.quality_notification",
            "Next": "NormalizeFHIRResources"
        },
        "NotifyQualityRemediationRequired": {
            "Type": "Task",
            "Resource": "arn:aws:lambda:us-east-2:596272105033:function:hi-quality-notifier",
            "Comment": "Quality below minimum threshold (0.60). Analytics cannot proceed. Notify client admin with specific field-level remediation steps.",
            "Parameters": {
                "tenant_id.$": "$.tenant_id",
                "quality_score.$": "$.composite_quality.score",
                "issues.$": "$.composite_quality.issues",
                "notification_type": "REMEDIATION_REQUIRED",
                "proceed": false
            },
            "ResultPath": "$.quality_notification",
            "Next": "WorkflowFailedQuality"
        },
        "WorkflowFailedQuality": {
            "Type": "Fail",
            "Error": "DataQualityBelowMinimumThreshold",
            "Cause": "Composite quality score below 0.60. Client must remediate data issues before analytics can proceed. Remediation report sent to client admin."
        },
        "NormalizeFHIRResources": {
            "Type": "Map",
            "Comment": "Transform extracted records to FHIR R4-compliant resources in parallel. Maps HL7 v2 codes to ICD-10/CPT/LOINC. Applies USCDI v3 required data elements. Tags all PHI fields. Writes lineage metadata.",
            "ItemsPath": "$.extraction_result.resource_types_extracted",
            "MaxConcurrency": 5,
            "Iterator": {
                "StartAt": "NormalizeSingleResourceType",
                "States": {
                    "NormalizeSingleResourceType": {
                        "Type": "Task",
                        "Resource": "arn:aws:lambda:us-east-2:596272105033:function:hi-fhir-normalizer",
                        "Parameters": {
                            "tenant_id.$": "$.tenant_id",
                            "resource_type.$": "$$.Map.Item.Value",
                            "source_location.$": "$.extraction_result.s3_location",
                            "normalization_config": {
                                "fhir_version": "R4",
                                "uscdi_version": "v3",
                                "code_systems": {
                                    "diagnoses": "ICD-10-CM",
                                    "procedures": "CPT",
                                    "observations": "LOINC"
                                },
                                "phi_tagging": true,
                                "lineage_tracking": true
                            }
                        },
                        "Retry": [
                            {
                                "ErrorEquals": [
                                    "Lambda.ServiceException",
                                    "NormalizationError"
                                ],
                                "IntervalSeconds": 5,
                                "MaxAttempts": 3,
                                "BackoffRate": 2
                            }
                        ],
                        "End": true
                    }
                }
            },
            "ResultPath": "$.normalization_results",
            "Next": "StoreNormalizedDataToLake"
        },
        "StoreNormalizedDataToLake": {
            "Type": "Task",
            "Resource": "arn:aws:lambda:us-east-2:596272105033:function:hi-data-lake-store",
            "Comment": "Writes normalized FHIR R4 resources to S3 analytics zone in Parquet format. Registers tables in AWS Glue catalog. Configures Athena workgroup per tenant. Enforces bucket-level tenant isolation via IAM.",
            "Parameters": {
                "tenant_id.$": "$.tenant_id",
                "batch_id.$": "$.batch_id",
                "normalized_files.$": "$.normalization_results",
                "target_format": "PARQUET",
                "glue_database.$": "States.Format('healthinsight_{}', $.tenant_id)"
            },
            "ResultPath": "$.data_lake_location",
            "Next": "RunParallelAnalysisPipelines"
        },
        "RunParallelAnalysisPipelines": {
            "Type": "Parallel",
            "Comment": "Run all three Bedrock Strands AI agent pipelines concurrently: claims risk analysis, patient risk stratification, and documentation gap analysis",
            "Branches": [
                {
                    "StartAt": "ClaimsAnalysisPipeline",
                    "States": {
                        "ClaimsAnalysisPipeline": {
                            "Type": "Map",
                            "Comment": "Analyze each claim for pre-submission denial risk using ClaimsAnalysisAgent (Bedrock Strands + Claude 3.5 Sonnet). Tools: predict_claim_denial_risk, check_modifier_requirements, analyze_denial_patterns, generate_correction_recommendations.",
                            "ItemsPath": "$.claims_to_analyze",
                            "MaxConcurrency": 10,
                            "Iterator": {
                                "StartAt": "RunClaimsAgent",
                                "States": {
                                    "RunClaimsAgent": {
                                        "Type": "Task",
                                        "Resource": "arn:aws:lambda:us-east-2:596272105033:function:hi-claims-analysis-agent",
                                        "Parameters": {
                                            "tenant_id.$": "$.tenant_id",
                                            "claim.$": "$$.Map.Item.Value",
                                            "agent_config": {
                                                "model_id": "us.anthropic.claude-3-5-sonnet-20241022-v2:0",
                                                "framework": "strands-agents",
                                                "tools": [
                                                    "predict_claim_denial_risk",
                                                    "check_modifier_requirements",
                                                    "analyze_denial_patterns",
                                                    "generate_correction_recommendations"
                                                ],
                                                "output_requires_human_review": true
                                            }
                                        },
                                        "TimeoutSeconds": 60,
                                        "Retry": [
                                            {
                                                "ErrorEquals": [
                                                    "BedrockThrottlingError"
                                                ],
                                                "IntervalSeconds": 10,
                                                "MaxAttempts": 5,
                                                "BackoffRate": 2
                                            }
                                        ],
                                        "End": true
                                    }
                                }
                            },
                            "End": true
                        }
                    }
                },
                {
                    "StartAt": "RiskStratificationPipeline",
                    "States": {
                        "RiskStratificationPipeline": {
                            "Type": "Task",
                            "Resource": "arn:aws:lambda:us-east-2:596272105033:function:hi-risk-stratification-agent",
                            "Comment": "Identifies high-risk patients using RiskStratificationAgent (Bedrock Strands). Analyzes visit patterns, chronic conditions, medication adherence, and social determinants. Produces prioritized care coordinator outreach list. ALL outputs require human review.",
                            "Parameters": {
                                "tenant_id.$": "$.tenant_id",
                                "patient_data_location.$": "$.data_lake_location.patient_path",
                                "encounter_data_location.$": "$.data_lake_location.encounter_path",
                                "agent_config": {
                                    "model_id": "us.anthropic.claude-3-5-sonnet-20241022-v2:0",
                                    "framework": "strands-agents",
                                    "risk_factors": [
                                        "ed_utilization_pattern",
                                        "chronic_conditions",
                                        "medication_adherence",
                                        "gap_in_care",
                                        "social_determinants"
                                    ],
                                    "output_type": "PRIORITIZED_OUTREACH_LIST",
                                    "output_requires_human_review": true
                                }
                            },
                            "TimeoutSeconds": 300,
                            "Retry": [
                                {
                                    "ErrorEquals": [
                                        "BedrockThrottlingError",
                                        "Lambda.ServiceException"
                                    ],
                                    "IntervalSeconds": 10,
                                    "MaxAttempts": 3,
                                    "BackoffRate": 2
                                }
                            ],
                            "End": true
                        }
                    }
                },
                {
                    "StartAt": "DocumentationGapPipeline",
                    "States": {
                        "DocumentationGapPipeline": {
                            "Type": "Task",
                            "Resource": "arn:aws:lambda:us-east-2:596272105033:function:hi-documentation-agent",
                            "Comment": "Checks encounter documentation completeness using DocumentationAgent (Bedrock Strands). Flags missing elements that may cause denials. Suggests appropriate ICD-10 and CPT codes based on documented diagnoses. Human review required before any coding action.",
                            "Parameters": {
                                "tenant_id.$": "$.tenant_id",
                                "encounter_data_location.$": "$.data_lake_location.encounter_path",
                                "agent_config": {
                                    "model_id": "us.anthropic.claude-3-5-sonnet-20241022-v2:0",
                                    "framework": "strands-agents",
                                    "checks": [
                                        "completeness",
                                        "medical_necessity_documentation",
                                        "code_suggestions",
                                        "prior_auth_flags"
                                    ],
                                    "output_type": "DOCUMENTATION_GAPS_REPORT",
                                    "output_requires_human_review": true
                                }
                            },
                            "TimeoutSeconds": 300,
                            "End": true
                        }
                    }
                }
            ],
            "ResultPath": "$.analysis_results",
            "Next": "MergeAndPrioritizeResults"
        },
        "MergeAndPrioritizeResults": {
            "Type": "Task",
            "Resource": "arn:aws:lambda:us-east-2:596272105033:function:hi-results-merger",
            "Comment": "Merges results from all three analysis pipelines. Deduplicates overlapping findings. Assigns overall risk classification per claim and patient. Computes total revenue at risk.",
            "Parameters": {
                "tenant_id.$": "$.tenant_id",
                "batch_id.$": "$.batch_id",
                "claims_analysis.$": "$.analysis_results[0]",
                "risk_stratification.$": "$.analysis_results[1]",
                "documentation_gaps.$": "$.analysis_results[2]"
            },
            "ResultPath": "$.merged_results",
            "Next": "ClassifyResultsByRiskLevel"
        },
        "ClassifyResultsByRiskLevel": {
            "Type": "Choice",
            "Comment": "Route based on highest risk level found. CRITICAL and HIGH require mandatory human review pause. MEDIUM surfaces to dashboard without pausing. LOW stores silently.",
            "Choices": [
                {
                    "Variable": "$.merged_results.has_critical_risk_items",
                    "BooleanEquals": true,
                    "Next": "EnqueueCriticalForReview"
                },
                {
                    "Variable": "$.merged_results.has_high_risk_items",
                    "BooleanEquals": true,
                    "Next": "EnqueueHighRiskForReview"
                },
                {
                    "Variable": "$.merged_results.has_medium_risk_items",
                    "BooleanEquals": true,
                    "Next": "StoreMediumRiskToDashboard"
                }
            ],
            "Default": "StoreLowRiskToDashboard"
        },
        "EnqueueCriticalForReview": {
            "Type": "Task",
            "Resource": "arn:aws:lambda:us-east-2:596272105033:function:hi-result-enqueuer",
            "Parameters": {
                "tenant_id.$": "$.tenant_id",
                "batch_id.$": "$.batch_id",
                "results.$": "$.merged_results",
                "priority": "CRITICAL",
                "sla_hours": 4,
                "escalation": true
            },
            "ResultPath": "$.queue_result",
            "Next": "MandatoryHumanReviewCritical"
        },
        "EnqueueHighRiskForReview": {
            "Type": "Task",
            "Resource": "arn:aws:lambda:us-east-2:596272105033:function:hi-result-enqueuer",
            "Parameters": {
                "tenant_id.$": "$.tenant_id",
                "batch_id.$": "$.batch_id",
                "results.$": "$.merged_results",
                "priority": "HIGH",
                "sla_hours": 24,
                "escalation": false
            },
            "ResultPath": "$.queue_result",
            "Next": "MandatoryHumanReviewHigh"
        },
        "MandatoryHumanReviewCritical": {
            "Type": "Task",
            "Resource": "arn:aws:states:::sqs:sendMessage.waitForTaskToken",
            "Comment": "MANDATORY GATE — CRITICAL RISK: Workflow is paused until a billing specialist explicitly approves or rejects each recommendation. SLA: 4 hours. No automated action is ever taken on AI outputs. Task token expires after 4 hours triggering escalation.",
            "Parameters": {
                "QueueUrl.$": "States.Format('https://sqs.us-east-2.amazonaws.com/596272105033/hi-review-critical-{}', $.tenant_id)",
                "MessageBody": {
                    "workflow_id.$": "$$.Execution.Name",
                    "batch_id.$": "$.batch_id",
                    "tenant_id.$": "$.tenant_id",
                    "task_token.$": "$$.Task.Token",
                    "priority": "CRITICAL",
                    "items_requiring_review.$": "$.merged_results.critical_risk_items",
                    "total_revenue_at_risk.$": "$.merged_results.total_revenue_at_risk",
                    "sla_deadline_hours": 4,
                    "review_instructions": "CRITICAL RISK: Each AI recommendation must be explicitly approved or rejected by an authorized billing specialist before any action is taken. HealthInsight AI outputs are decision-support tools only — no autonomous action is permitted. Approve, reject, or escalate each item individually.",
                    "ai_model_disclosure": "Recommendations generated by Bedrock Strands Agents using Claude 3.5 Sonnet. All outputs are human-reviewable operational support, not clinical decisions."
                },
                "MessageAttributes": {
                    "priority": {
                        "DataType": "String",
                        "StringValue": "CRITICAL"
                    },
                    "tenant_id": {
                        "DataType": "String",
                        "StringValue.$": "$.tenant_id"
                    }
                }
            },
            "HeartbeatSeconds": 14400,
            "ResultPath": "$.human_review_decision",
            "Catch": [
                {
                    "ErrorEquals": [
                        "States.HeartbeatTimeout"
                    ],
                    "Next": "HandleCriticalReviewTimeout",
                    "ResultPath": "$.timeout_error"
                }
            ],
            "Next": "ValidateReviewDecision"
        },
        "MandatoryHumanReviewHigh": {
            "Type": "Task",
            "Resource": "arn:aws:states:::sqs:sendMessage.waitForTaskToken",
            "Comment": "MANDATORY GATE — HIGH RISK: Workflow paused for human review. SLA: 24 hours. Same enforcement as CRITICAL — no autonomous action on AI outputs.",
            "Parameters": {
                "QueueUrl.$": "States.Format('https://sqs.us-east-2.amazonaws.com/596272105033/hi-review-standard-{}', $.tenant_id)",
                "MessageBody": {
                    "workflow_id.$": "$$.Execution.Name",
                    "batch_id.$": "$.batch_id",
                    "tenant_id.$": "$.tenant_id",
                    "task_token.$": "$$.Task.Token",
                    "priority": "HIGH",
                    "items_requiring_review.$": "$.merged_results.high_risk_items",
                    "total_revenue_at_risk.$": "$.merged_results.total_revenue_at_risk",
                    "sla_deadline_hours": 24,
                    "review_instructions": "HIGH RISK: Billing specialist must review and approve each recommendation before any claim is modified or resubmitted. AI outputs are informational only."
                }
            },
            "HeartbeatSeconds": 86400,
            "ResultPath": "$.human_review_decision",
            "Catch": [
                {
                    "ErrorEquals": [
                        "States.HeartbeatTimeout"
                    ],
                    "Next": "HandleReviewTimeout",
                    "ResultPath": "$.timeout_error"
                }
            ],
            "Next": "ValidateReviewDecision"
        },
        "HandleCriticalReviewTimeout": {
            "Type": "Task",
            "Resource": "arn:aws:lambda:us-east-2:596272105033:function:hi-review-escalator",
            "Comment": "CRITICAL review SLA exceeded. Escalate immediately to supervisor with full context. Mark all items as PENDING_ESCALATION.",
            "Parameters": {
                "tenant_id.$": "$.tenant_id",
                "batch_id.$": "$.batch_id",
                "timeout_error.$": "$.timeout_error",
                "escalation_level": "SUPERVISOR",
                "priority": "CRITICAL"
            },
            "ResultPath": "$.escalation_result",
            "Next": "UpdateAuditTrail"
        },
        "HandleReviewTimeout": {
            "Type": "Task",
            "Resource": "arn:aws:lambda:us-east-2:596272105033:function:hi-review-escalator",
            "Comment": "Review SLA exceeded. Notify billing supervisor and re-queue for review.",
            "Parameters": {
                "tenant_id.$": "$.tenant_id",
                "batch_id.$": "$.batch_id",
                "timeout_error.$": "$.timeout_error",
                "escalation_level": "TEAM_LEAD"
            },
            "ResultPath": "$.escalation_result",
            "Next": "UpdateAuditTrail"
        },
        "ValidateReviewDecision": {
            "Type": "Choice",
            "Comment": "Route based on the billing specialist's explicit decision for each reviewed item",
            "Choices": [
                {
                    "Variable": "$.human_review_decision.decision",
                    "StringEquals": "APPROVED",
                    "Next": "ProcessApprovedRecommendations"
                },
                {
                    "Variable": "$.human_review_decision.decision",
                    "StringEquals": "PARTIALLY_APPROVED",
                    "Next": "ProcessApprovedRecommendations"
                },
                {
                    "Variable": "$.human_review_decision.decision",
                    "StringEquals": "REJECTED",
                    "Next": "LogRejectedForFeedback"
                }
            ],
            "Default": "LogRejectedForFeedback"
        },
        "ProcessApprovedRecommendations": {
            "Type": "Task",
            "Resource": "arn:aws:lambda:us-east-2:596272105033:function:hi-apply-approved-actions",
            "Comment": "Applies ONLY the recommendations explicitly approved by the human reviewer. Partial approvals handled item-by-item. Records reviewer ID, decision, and timestamp in immutable audit log.",
            "Parameters": {
                "tenant_id.$": "$.tenant_id",
                "batch_id.$": "$.batch_id",
                "approved_items.$": "$.human_review_decision.approved_items",
                "reviewer_id.$": "$.human_review_decision.reviewer_id",
                "reviewed_at.$": "$.human_review_decision.reviewed_at",
                "override_reason.$": "$.human_review_decision.override_reason"
            },
            "ResultPath": "$.applied_actions",
            "Retry": [
                {
                    "ErrorEquals": [
                        "Lambda.ServiceException"
                    ],
                    "IntervalSeconds": 5,
                    "MaxAttempts": 3,
                    "BackoffRate": 2
                }
            ],
            "Next": "UpdateAuditTrail"
        },
        "LogRejectedForFeedback": {
            "Type": "Task",
            "Resource": "arn:aws:lambda:us-east-2:596272105033:function:hi-rejection-logger",
            "Comment": "Records reviewer rejection with reason. Feeds rejected examples back into model improvement pipeline for fine-tuning future Strands agent behavior.",
            "Parameters": {
                "tenant_id.$": "$.tenant_id",
                "batch_id.$": "$.batch_id",
                "rejected_items.$": "$.human_review_decision.rejected_items",
                "rejection_reasons.$": "$.human_review_decision.rejection_reasons",
                "reviewer_id.$": "$.human_review_decision.reviewer_id"
            },
            "ResultPath": "$.rejection_log",
            "Next": "UpdateAuditTrail"
        },
        "StoreMediumRiskToDashboard": {
            "Type": "Task",
            "Resource": "arn:aws:lambda:us-east-2:596272105033:function:hi-dashboard-store",
            "Comment": "MEDIUM risk items surfaced in client dashboard for optional billing team review. No workflow pause. Dashboard alert generated.",
            "Parameters": {
                "tenant_id.$": "$.tenant_id",
                "results.$": "$.merged_results",
                "priority": "MEDIUM",
                "alert_type": "DASHBOARD_WARNING"
            },
            "ResultPath": "$.store_result",
            "Next": "UpdateAuditTrail"
        },
        "StoreLowRiskToDashboard": {
            "Type": "Task",
            "Resource": "arn:aws:lambda:us-east-2:596272105033:function:hi-dashboard-store",
            "Comment": "LOW risk — all claims appear well-coded. Results stored in dashboard. No alerts generated.",
            "Parameters": {
                "tenant_id.$": "$.tenant_id",
                "results.$": "$.merged_results",
                "priority": "LOW",
                "alert_type": "NONE"
            },
            "ResultPath": "$.store_result",
            "Next": "UpdateAuditTrail"
        },
        "UpdateAuditTrail": {
            "Type": "Task",
            "Resource": "arn:aws:lambda:us-east-2:596272105033:function:hi-audit-writer",
            "Comment": "Writes complete structured audit trail to CloudWatch Logs with 7-year retention (HIPAA Security Rule 164.312(b)). Records: who extracted what PHI, which AI model processed it, who reviewed the output, what decision was made, and when.",
            "Parameters": {
                "tenant_id.$": "$.tenant_id",
                "batch_id.$": "$.batch_id",
                "workflow_id.$": "$$.Execution.Name",
                "phi_access_log.$": "$.phi_classification.access_log",
                "ai_usage": {
                    "model_id": "us.anthropic.claude-3-5-sonnet-20241022-v2:0",
                    "framework": "strands-agents",
                    "human_review_enforced": true,
                    "autonomous_action_taken": false
                },
                "audit_events": {
                    "extraction_completed": true,
                    "phi_classified": true,
                    "quality_assessed": true,
                    "normalization_completed": true,
                    "analysis_completed": true,
                    "human_review_completed": true
                }
            },
            "ResultPath": "$.audit_result",
            "Next": "UpdateDashboardMetrics"
        },
        "UpdateDashboardMetrics": {
            "Type": "Task",
            "Resource": "arn:aws:lambda:us-east-2:596272105033:function:hi-metrics-updater",
            "Comment": "Updates client dashboard: denial risk summary, quality scores, patient risk list, documentation gaps. Publishes CloudWatch metrics for operational monitoring.",
            "Parameters": {
                "tenant_id.$": "$.tenant_id",
                "batch_id.$": "$.batch_id",
                "metrics": {
                    "claims_analyzed.$": "$.merged_results.total_claims",
                    "high_risk_count.$": "$.merged_results.high_risk_count",
                    "revenue_at_risk.$": "$.merged_results.total_revenue_at_risk",
                    "quality_score.$": "$.composite_quality.score",
                    "patients_stratified.$": "$.analysis_results[1].patients_analyzed",
                    "documentation_gaps_found.$": "$.analysis_results[2].gaps_found"
                }
            },
            "ResultPath": "$.dashboard_update",
            "Next": "SendCompletionNotification"
        },
        "SendCompletionNotification": {
            "Type": "Task",
            "Resource": "arn:aws:states:::sns:publish",
            "Comment": "Notifies client billing admin and relevant staff that processing is complete and results are ready in the dashboard",
            "Parameters": {
                "TopicArn.$": "States.Format('arn:aws:sns:us-east-2:596272105033:hi-notifications-{}', $.tenant_id)",
                "Subject": "HealthInsight AI — Processing Complete",
                "Message.$": "States.Format('Batch {} complete. {} claims analyzed. {} high-risk items queued for review. Revenue at risk: ${}. Quality score: {}/1.0.', $.batch_id, $.merged_results.total_claims, $.merged_results.high_risk_count, $.merged_results.total_revenue_at_risk, $.composite_quality.score)"
            },
            "ResultPath": "$.notification_result",
            "Next": "WorkflowComplete"
        },
        "NotifyExtractionFailure": {
            "Type": "Task",
            "Resource": "arn:aws:lambda:us-east-2:596272105033:function:hi-extraction-failure-notifier",
            "Comment": "All extraction strategies failed. Notify tenant admin with specific error details and remediation steps.",
            "Parameters": {
                "tenant_id.$": "$.tenant_id",
                "batch_id.$": "$.batch_id",
                "error.$": "$.error",
                "remediation_steps": [
                    "Verify FHIR API endpoint URL and client credentials are current",
                    "Check HL7 feed connectivity and message queue backlog",
                    "Confirm flat file upload completed to the tenant S3 ingestion prefix",
                    "Contact HealthInsight support if all sources remain unavailable"
                ]
            },
            "Next": "WorkflowFailed"
        },
        "WorkflowComplete": {
            "Type": "Succeed",
            "Comment": "Workflow completed successfully. All results stored in client dashboard. Human review queue populated where required. Audit trail written."
        },
        "WorkflowFailed": {
            "Type": "Fail",
            "Error": "WorkflowExecutionError",
            "Cause": "HealthInsight AI workflow encountered an unrecoverable error. Review CloudWatch Logs for the specific failure point and corrective action."
        }
    }
}