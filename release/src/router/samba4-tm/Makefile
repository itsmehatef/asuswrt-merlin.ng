# Standalone Samba with Apple SMB extensions for the GT-AXE16000.
# Samba dependencies use a private prefix. Preserve the baseline global
# libatomic loader path with a link to the identical vendor library.
TM_ROOT ?= $(CURDIR)/.build
export TM_ROOT

.PHONY: all install clean distclean

all:
	bash ./build.sh all

install: all
	test -n "$(INSTALLDIR)"
	bash ./build.sh install "$(INSTALLDIR)"
	# The SDK collector otherwise sees the private copy and omits /lib/libatomic.so.1.
	test -f "$(INSTALLDIR)/usr/lib/tm-samba/libatomic.so.1"
	mkdir -p "$(INSTALLDIR)/lib"
	@if [ -L "$(INSTALLDIR)/lib/libatomic.so.1" ]; then \
		if [ "$$(readlink "$(INSTALLDIR)/lib/libatomic.so.1")" != "../usr/lib/tm-samba/libatomic.so.1" ]; then \
			echo "Refusing conflicting libatomic link: $(INSTALLDIR)/lib/libatomic.so.1" >&2; exit 1; \
		fi; \
	elif [ -e "$(INSTALLDIR)/lib/libatomic.so.1" ]; then \
		echo "Refusing existing libatomic destination: $(INSTALLDIR)/lib/libatomic.so.1" >&2; exit 1; \
	else \
		ln -s ../usr/lib/tm-samba/libatomic.so.1 "$(INSTALLDIR)/lib/libatomic.so.1"; \
	fi

# Keep downloaded source archives and dependency builds for reproducible rebuilds.
clean:
	bash ./build.sh clean

distclean: clean
