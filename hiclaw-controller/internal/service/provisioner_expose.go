package service

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"os"

	v1beta1 "github.com/hiclaw/hiclaw-controller/api/v1beta1"
	"github.com/hiclaw/hiclaw-controller/internal/gateway"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

// --- Port Exposure ---

// workerNameHash returns a short hex digest of the worker name, suitable for
// use in K8s labels (which have a 63-character name limit).
func workerNameHash(name string) string {
	h := sha256.Sum256([]byte(name))
	return hex.EncodeToString(h[:4]) // 8 hex chars
}

// DomainForExpose returns the domain name for a worker's exposed port.
// Uses a short hash of the worker name to keep the domain (and resulting
// Higress label higress.io/domain_<domain>) within K8s' 63-char limit.
//
// The domain template is configurable via HICLAW_EXPOSE_DOMAIN_TEMPLATE.
// The template receives two args: (hash, port).
// Default: w-%s-%d-preview.cloud.hropenai.cn
func DomainForExpose(workerName string, port int) string {
	tmpl := os.Getenv("HICLAW_EXPOSE_DOMAIN_TEMPLATE")
	if tmpl == "" {
		tmpl = "w-%s-%d-preview.cloud.hropenai.cn"
	}
	return fmt.Sprintf(tmpl, workerNameHash(workerName), port)
}

// ContainerDNSName returns the FQDN for a worker container that Higress can resolve.
// In embedded/Docker mode this is <name>.local (Docker built-in DNS).
func ContainerDNSName(workerName string) string {
	return fmt.Sprintf("%s.local", workerName)
}

// ReconcileExpose compares desired expose ports with current status, creates new
// gateway resources for added ports, and removes resources for deleted ports.
// In Kubernetes (incluster) mode, it uses the pod's IP with a static service source
// because .local DNS resolution is a Docker convention that does not work in K8s.
func (p *Provisioner) ReconcileExpose(ctx context.Context, workerName string, desired []v1beta1.ExposePort, current []v1beta1.ExposedPortStatus) ([]v1beta1.ExposedPortStatus, error) {
	if p.gateway == nil {
		return current, nil
	}

	desiredSet := make(map[int]v1beta1.ExposePort)
	for _, ep := range desired {
		desiredSet[ep.Port] = ep
	}
	currentSet := make(map[int]v1beta1.ExposedPortStatus)
	for _, ep := range current {
		currentSet[ep.Port] = ep
	}

	// In K8s mode, look up the pod IP so Higress can use a static service source
	// instead of DNS resolution (which doesn't work with .local in K8s).
	serviceHost := ContainerDNSName(workerName)
	if p.kubeMode == "incluster" {
		podIP, err := p.getWorkerPodIP(ctx, workerName)
		if err == nil && podIP != "" {
			serviceHost = podIP
		}
		// If pod IP lookup fails, fall back to DNS name (degraded — 503 expected)
	}

	var result []v1beta1.ExposedPortStatus
	var firstErr error

	// In K8s mode, always re-apply existing ports so static service sources
	// are updated when the pod IP changes. In Docker mode, skip existing ports
	// because DNS resolution handles IP changes automatically.
	for _, ep := range desired {
		if _, exists := currentSet[ep.Port]; exists && p.kubeMode != "incluster" {
			result = append(result, currentSet[ep.Port])
			continue
		}

		domain := DomainForExpose(workerName, ep.Port)
		err := p.gateway.ExposePort(ctx, gateway.PortExposeRequest{
			WorkerName:  workerName,
			ServiceHost: serviceHost,
			Port:        ep.Port,
			Domain:      domain,
		})
		if err != nil {
			if firstErr == nil {
				firstErr = fmt.Errorf("expose port %d: %w", ep.Port, err)
			}
			continue
		}

		result = append(result, v1beta1.ExposedPortStatus{
			Port:   ep.Port,
			Domain: domain,
		})
	}

	for _, ep := range current {
		if _, stillDesired := desiredSet[ep.Port]; stillDesired {
			continue
		}

		err := p.gateway.UnexposePort(ctx, gateway.PortExposeRequest{
			WorkerName: workerName,
			Port:       ep.Port,
			Domain:     ep.Domain,
		})
		if err != nil {
			if firstErr == nil {
				firstErr = fmt.Errorf("unexpose port %d: %w", ep.Port, err)
			}
		}
	}

	return result, firstErr
}

// getWorkerPodIP returns the pod IP for a worker in K8s mode.
// Uses the existing hiclaw.io/worker label set on all worker pods.
func (p *Provisioner) getWorkerPodIP(ctx context.Context, workerName string) (string, error) {
	if p.k8sClient == nil {
		return "", fmt.Errorf("k8s client not available")
	}

	pods, err := p.k8sClient.CoreV1().Pods(p.namespace).List(ctx, metav1.ListOptions{
		LabelSelector: fmt.Sprintf("hiclaw.io/worker=%s", workerName),
	})
	if err != nil {
		return "", fmt.Errorf("list pods: %w", err)
	}

	for _, pod := range pods.Items {
		if pod.Status.PodIP != "" {
			return pod.Status.PodIP, nil
		}
	}

	return "", fmt.Errorf("pod not found or no IP assigned for worker %s", workerName)
}
