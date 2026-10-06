// Local bridge: credentials and cookies stay in memory for one operation.
package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"net/http/cookiejar"
	"os"
	"sync"
	"time"

	"github.com/majd/ipatool/v2/internal/sap"
	"github.com/majd/ipatool/v2/pkg/appstore"
	"github.com/majd/ipatool/v2/pkg/util/machine"
	"github.com/majd/ipatool/v2/pkg/util/operatingsystem"
	"github.com/schollz/progressbar/v3"
)

const bundleID = "com.feelthemusi.musi"
const appID int64 = 591560124

type memoryKeys map[string][]byte

func (m memoryKeys) Get(k string) ([]byte, error) {
	v, ok := m[k]
	if !ok {
		return nil, os.ErrNotExist
	}
	return v, nil
}
func (m memoryKeys) Set(k string, v []byte) error { m[k] = append([]byte(nil), v...); return nil }
func (m memoryKeys) Remove(k string) error        { delete(m, k); return nil }

type memoryCookies struct{ *cookiejar.Jar }

func (m memoryCookies) Save() error { return nil }

type request struct {
	Email    string `json:"email"`
	Password string `json:"password"`
	Code     string `json:"code"`
	Output   string `json:"output"`
}
type event struct {
	Type     string  `json:"type"`
	Message  string  `json:"message,omitempty"`
	Path     string  `json:"path,omitempty"`
	Stage    string  `json:"stage,omitempty"`
	State    string  `json:"state,omitempty"`
	Progress float64 `json:"progress,omitempty"`
	Bytes    int64   `json:"bytes,omitempty"`
	Total    int64   `json:"total,omitempty"`
	Speed    float64 `json:"speed,omitempty"`
	ETA      float64 `json:"eta,omitempty"`
}

var encoder = json.NewEncoder(os.Stdout)
var outputLock sync.Mutex

func send(e event)                    { outputLock.Lock(); defer outputLock.Unlock(); _ = encoder.Encode(e) }
func emit(kind, message, path string) { send(event{Type: kind, Message: message, Path: path}) }
func stage(name, state, message string) {
	send(event{Type: "stage", Stage: name, State: state, Message: message})
}

func downloadWithProgress(store appstore.AppStore, input appstore.DownloadInput) (appstore.DownloadOutput, error) {
	bar := progressbar.NewOptions64(1, progressbar.OptionSetWriter(io.Discard), progressbar.OptionThrottle(200*time.Millisecond))
	input.Progress = bar
	stop := make(chan struct{})
	joined := make(chan struct{})
	go func() {
		defer close(joined)
		ticker := time.NewTicker(250 * time.Millisecond)
		defer ticker.Stop()
		var first time.Time
		var initialBytes float64
		prepared := false
		for {
			select {
			case <-stop:
				return
			case <-ticker.C:
				s := bar.State()
				if s.CurrentBytes <= 0 {
					continue
				}
				if first.IsZero() {
					first = time.Now()
					initialBytes = s.CurrentBytes
				}
				fraction := s.CurrentPercent
				if !prepared && fraction > 0 && fraction <= 1 && !math.IsNaN(fraction) && !math.IsInf(fraction, 0) {
					total := int64(s.CurrentBytes / fraction)
					speed := (s.CurrentBytes - initialBytes) / time.Since(first).Seconds()
					eta := float64(0)
					if speed > 0 {
						eta = float64(total-int64(s.CurrentBytes)) / speed
					}
					if time.Since(first) < time.Second {
						speed = 0
						eta = 0
					}
					send(event{Type: "progress", Stage: "download", Progress: fraction, Bytes: int64(s.CurrentBytes), Total: total, Speed: speed, ETA: eta})
					if fraction >= 1 && !prepared {
						stage("download", "done", "Download complete")
						stage("prepare", "active", "Preparing your original app package")
						prepared = true
					}
				}
			}
		}
	}()
	out, err := store.Download(input)
	close(stop)
	<-joined
	if err == nil {
		stage("download", "done", "Download complete")
		stage("prepare", "active", "Preserving the original app signature")
	}
	return out, err
}

func diagnostics() error {
	operatingSystem := operatingsystem.New()
	jar, _ := cookiejar.New(nil)
	store := appstore.NewAppStore(appstore.Args{Keychain: memoryKeys{}, CookieJar: memoryCookies{jar}, OperatingSystem: operatingSystem, Machine: machine.New(machine.Args{OS: operatingSystem})})
	bag, err := store.Bag(appstore.BagInput{})
	if err != nil {
		return err
	}
	emit("status", "Apple endpoint received: "+bag.AuthEndpoint, "")
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Minute)
	defer cancel()
	signer, err := sap.NewSigner(ctx, sap.Config{SetupURL: bag.SAPConfig.SetupURL, CertificateURL: bag.SAPConfig.CertificateURL, Version: bag.SAPConfig.Version, HardwareID: []byte{2, 0, 0, 0, 0, 1}})
	if err != nil {
		return err
	}
	defer signer.Close()
	signature, err := signer.Sign([]byte("musi-installer local diagnostics"))
	if err != nil {
		return err
	}
	if len(signature) == 0 {
		return errors.New("SAP signature is empty")
	}
	emit("status", "Apple configuration and SAP signing verified. No account was used.", "")
	return nil
}

func run(input io.Reader) error {
	decoder := json.NewDecoder(io.LimitReader(input, 1024*1024))
	var req request
	if err := decoder.Decode(&req); err != nil {
		return errors.New("invalid local request")
	}
	if req.Email == "" || req.Password == "" || req.Output == "" {
		return errors.New("email, password and output path are required")
	}
	jar, err := cookiejar.New(nil)
	if err != nil {
		return err
	}
	operatingSystem := operatingsystem.New()
	store := appstore.NewAppStore(appstore.Args{Keychain: memoryKeys{}, CookieJar: memoryCookies{jar}, OperatingSystem: operatingSystem, Machine: machine.New(machine.Args{OS: operatingSystem})})
	defer store.Revoke() // Only in-memory entries, no changes to another ipatool session.
	stage("signin", "active", "Connecting securely to Apple. First sign-in may take a few minutes.")
	authenticate := func() (appstore.LoginOutput, error) {
		var result appstore.LoginOutput
		var loginErr error
		for attempt := 0; attempt < 3; attempt++ {
			result, loginErr = store.Login(appstore.LoginInput{Email: req.Email, Password: req.Password, AuthCode: req.Code})
			if !errors.Is(loginErr, appstore.ErrAuthCodeRequired) || attempt == 2 {
				return result, loginErr
			}
			stage("signin", "waiting", "Waiting for your verification code")
			emit("2fa", "Enter your six-digit Apple verification code.", "")
			var next request
			if decoder.Decode(&next) != nil {
				return result, errors.New("verification cancelled")
			}
			req.Code = next.Code
			stage("signin", "active", "Verifying your code with Apple")
		}
		return result, loginErr
	}
	login, err := authenticate()
	if err != nil {
		return fmt.Errorf("Apple sign-in: %w", err)
	}
	app := appstore.App{ID: appID, BundleID: bundleID}
	stage("signin", "done", "Signed in successfully")
	stage("download", "active", "Requesting your copy of Musi from Apple")
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Minute)
	defer cancel()
	out, err := downloadWithProgress(store, appstore.DownloadInput{Context: ctx, Account: login.Account, App: app, OutputPath: req.Output, Platform: appstore.PlatformIPhone})
	if errors.Is(err, appstore.ErrPasswordTokenExpired) {
		req.Code = ""
		stage("signin", "active", "Refreshing your Apple session")
		login, err = authenticate()
		if err == nil {
			stage("signin", "done", "Session refreshed")
			stage("download", "active", "Requesting your copy from Apple")
			out, err = downloadWithProgress(store, appstore.DownloadInput{Context: ctx, Account: login.Account, App: app, OutputPath: req.Output, Platform: appstore.PlatformIPhone})
		}
	}
	if err != nil {
		if errors.Is(err, appstore.ErrLicenseRequired) {
			return errors.New("Apple did not authorize this account to download Musi (license required). No purchase was made")
		}
		return fmt.Errorf("Apple download: %w", err)
	}
	if err = store.ReplicateSinf(appstore.ReplicateSinfInput{Sinfs: out.Sinfs, PackagePath: out.DestinationPath}); err != nil {
		return fmt.Errorf("original package signature: %w", err)
	}
	stage("prepare", "done", "Your original app package is ready")
	emit("downloaded", "Download complete", out.DestinationPath)
	return nil
}

func main() {
	if len(os.Args) > 1 && os.Args[1] == "--version" {
		fmt.Println("musi-bridge 2.0 / ipatool cde7d00 / SAP / direct app ID 591560124 / progress events")
		return
	}
	if len(os.Args) > 1 && (os.Args[1] == "--diagnostics" || os.Args[1] == "--diagnostica") {
		if err := diagnostics(); err != nil {
			emit("error", err.Error(), "")
			os.Exit(1)
		}
		return
	}
	if err := run(os.Stdin); err != nil {
		emit("error", err.Error(), "")
		os.Exit(1)
	}
}
